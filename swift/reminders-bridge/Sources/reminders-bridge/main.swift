import EventKit
import Foundation

func eprint(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

let store = EKEventStore()

func requestAccess() -> Bool {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    store.requestFullAccessToReminders { ok, error in
        granted = ok
        if let error = error {
            eprint("access request error: \(error)")
        }
        sem.signal()
    }
    sem.wait()
    return granted
}

func findCalendar(named name: String, createIfMissing: Bool) -> EKCalendar? {
    if let existing = store.calendars(for: .reminder).first(where: { $0.title == name }) {
        return existing
    }
    guard createIfMissing else { return nil }
    guard let source = store.defaultCalendarForNewReminders()?.source
        ?? store.sources.first(where: { $0.sourceType == .local })
        ?? store.sources.first
    else { return nil }
    let cal = EKCalendar(for: .reminder, eventStore: store)
    cal.title = name
    cal.source = source
    do {
        try store.saveCalendar(cal, commit: true)
        return cal
    } catch {
        eprint("saveCalendar failed: \(error)")
        return nil
    }
}

// Looks up a reminder by id, scoped to a specific list. Reminder ids are
// otherwise globally addressable via calendarItem(withIdentifier:), which
// would let a caller read/mutate any reminder anywhere if it ever received
// an id from outside the intended list (e.g. a tampered or stale state
// file) — this keeps every id-based operation confined to the list it's
// supposed to operate on.
func findReminder(id: String, inList listName: String) -> EKReminder? {
    guard let item = store.calendarItem(withIdentifier: id) as? EKReminder else { return nil }
    guard item.calendar.title == listName else { return nil }
    return item
}

func fetchReminders(in calendar: EKCalendar) -> [EKReminder] {
    let predicate = store.predicateForReminders(in: [calendar])
    let sem = DispatchSemaphore(value: 0)
    var result: [EKReminder] = []
    _ = store.fetchReminders(matching: predicate) { reminders in
        result = reminders ?? []
        sem.signal()
    }
    sem.wait()
    return result
}

// Wire format for due dates is "yyyy-MM-ddTHH:mm:ss" with no timezone —
// matching the naive/local datetimes used throughout the Python side and
// AppleScript's local wall-clock dates. Parsing/formatting these as literal
// calendar components (never through Date/ISO8601DateFormatter, which are
// instant-based and would silently shift the date across timezone
// boundaries — exactly the bug this caused during testing) keeps it
// unambiguous.
func parseNaiveDateTime(_ s: String) -> DateComponents? {
    let parts = s.split(separator: "T")
    guard parts.count == 2 else { return nil }
    let dateParts = parts[0].split(separator: "-").compactMap { Int($0) }
    let timeParts = parts[1].split(separator: ":").compactMap { Int($0.prefix(2)) }
    guard dateParts.count == 3, timeParts.count >= 2 else { return nil }
    var comps = DateComponents()
    comps.year = dateParts[0]
    comps.month = dateParts[1]
    comps.day = dateParts[2]
    comps.hour = timeParts[0]
    comps.minute = timeParts[1]
    comps.second = timeParts.count >= 3 ? timeParts[2] : 0
    return comps
}

func formatNaiveDateTime(_ comps: DateComponents) -> String? {
    guard let y = comps.year, let mo = comps.month, let d = comps.day else { return nil }
    let h = comps.hour ?? 0
    let mi = comps.minute ?? 0
    let s = comps.second ?? 0
    return String(format: "%04d-%02d-%02dT%02d:%02d:%02d", y, mo, d, h, mi, s)
}

// A reminder's due date is "all-day" (date only, no specific time) when its
// dueDateComponents carry no hour — matches how EventKit itself represents
// the distinction, unlike Reminders.app's AppleScript dictionary which
// always populates a time component regardless.
func dueDateComponents(fromWire wire: String, allDay: Bool) -> DateComponents? {
    guard var comps = parseNaiveDateTime(wire) else { return nil }
    if allDay {
        comps.hour = nil
        comps.minute = nil
        comps.second = nil
    }
    return comps
}

struct ReminderJSON: Codable {
    let id: String
    let name: String
    let body: String
    let completed: Bool
    let due: String?
    let allDay: Bool
}

func toJSON(_ r: EKReminder) -> ReminderJSON {
    var due: String? = nil
    var allDay = false
    if let comps = r.dueDateComponents {
        due = formatNaiveDateTime(comps)
        allDay = comps.hour == nil
    }
    return ReminderJSON(
        id: r.calendarItemIdentifier,
        name: r.title ?? "",
        body: r.notes ?? "",
        completed: r.isCompleted,
        due: due,
        allDay: allDay
    )
}

func printJSON<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    guard let data = try? encoder.encode(value) else {
        eprint("encode failed")
        exit(1)
    }
    print(String(data: data, encoding: .utf8)!)
}

func opt(_ args: [String], _ name: String) -> String? {
    guard let idx = args.firstIndex(of: "--\(name)"), idx + 1 < args.count else { return nil }
    return args[idx + 1]
}

func flag(_ args: [String], _ name: String) -> Bool {
    args.contains("--\(name)")
}

guard requestAccess() else {
    eprint("Reminders access not granted. Check System Settings > Privacy & Security > Reminders.")
    exit(1)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    eprint("usage: reminders-bridge <command> [options]")
    exit(1)
}
let rest = Array(args.dropFirst())

switch command {
case "get-reminders":
    guard let listName = opt(rest, "list"), let cal = findCalendar(named: listName, createIfMissing: true) else {
        print("[]")
        break
    }
    printJSON(fetchReminders(in: cal).map(toJSON))

case "get-reminder":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName)
    else {
        print("null")
        break
    }
    printJSON(toJSON(item))

case "create-reminder":
    guard let listName = opt(rest, "list"), let cal = findCalendar(named: listName, createIfMissing: true) else {
        eprint("list not found/creatable")
        exit(1)
    }
    let reminder = EKReminder(eventStore: store)
    reminder.calendar = cal
    reminder.title = opt(rest, "name") ?? ""
    reminder.notes = opt(rest, "body") ?? ""
    if let dueStr = opt(rest, "due"), let comps = dueDateComponents(fromWire: dueStr, allDay: flag(rest, "all-day")) {
        reminder.dueDateComponents = comps
    }
    do {
        try store.save(reminder, commit: true)
        printJSON(["id": reminder.calendarItemIdentifier])
    } catch {
        eprint("save failed: \(error)")
        exit(1)
    }

case "set-body":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName)
    else {
        printJSON(["ok": false])
        break
    }
    item.notes = opt(rest, "body") ?? ""
    do {
        try store.save(item, commit: true)
        printJSON(["ok": true])
    } catch {
        eprint("save failed: \(error)")
        printJSON(["ok": false])
    }

case "set-name":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName)
    else {
        printJSON(["ok": false])
        break
    }
    item.title = opt(rest, "name") ?? ""
    do {
        try store.save(item, commit: true)
        printJSON(["ok": true])
    } catch {
        eprint("save failed: \(error)")
        printJSON(["ok": false])
    }

case "complete":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName)
    else {
        printJSON(["ok": false])
        break
    }
    item.isCompleted = true
    do {
        try store.save(item, commit: true)
        printJSON(["ok": true])
    } catch {
        eprint("save failed: \(error)")
        printJSON(["ok": false])
    }

case "uncomplete":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName)
    else {
        printJSON(["ok": false])
        break
    }
    item.isCompleted = false
    do {
        try store.save(item, commit: true)
        printJSON(["ok": true])
    } catch {
        eprint("save failed: \(error)")
        printJSON(["ok": false])
    }

case "set-due":
    guard let listName = opt(rest, "list"), let id = opt(rest, "id"),
          let item = findReminder(id: id, inList: listName),
          let dueStr = opt(rest, "due"), let comps = dueDateComponents(fromWire: dueStr, allDay: flag(rest, "all-day"))
    else {
        printJSON(["ok": false])
        break
    }
    item.dueDateComponents = comps
    do {
        try store.save(item, commit: true)
        printJSON(["ok": true])
    } catch {
        eprint("save failed: \(error)")
        printJSON(["ok": false])
    }

default:
    eprint("unknown command: \(command)")
    exit(1)
}
