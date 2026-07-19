from __future__ import annotations

from datetime import datetime, timedelta, timezone

from todoist_api_python.api import TodoistAPI


class TodoistBridge:
    """Talks to the Todoist API for a single dedicated project."""

    def __init__(self, api_token: str):
        self.api = TodoistAPI(api_token)

    def get_or_create_project(self, name: str) -> str:
        for page in self.api.get_projects():
            for project in page:
                if project.name == name:
                    return project.id
        return self.api.add_project(name=name).id

    def get_active_tasks(self, project_id: str) -> list:
        tasks = []
        for page in self.api.get_tasks(project_id=project_id):
            tasks.extend(page)
        return tasks

    def get_recently_completed_tasks(self, project_id: str, since_days: int) -> list:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        until = datetime.now(timezone.utc)
        tasks = []
        for page in self.api.get_completed_tasks_by_completion_date(since=since, until=until):
            tasks.extend(t for t in page if t.project_id == project_id)
        return tasks

    def create_task(self, project_id: str, content: str, description: str) -> str:
        return self.api.add_task(content=content, project_id=project_id, description=description).id

    def set_task_description(self, task_id: str, description: str) -> None:
        self.api.update_task(task_id, description=description)

    def complete_task(self, task_id: str) -> None:
        self.api.complete_task(task_id)
