from __future__ import annotations

import logging
from datetime import datetime

import httpx
from todoist_api_python.api import TodoistAPI

log = logging.getLogger(__name__)


class TodoistBridge:
    """Talks to the Todoist API for a single dedicated project."""

    def __init__(self, api_token: str):
        self.api = TodoistAPI(api_token)

    def get_or_create_project(self, name: str) -> str:
        for page in self.api.get_projects():
            for project in page:
                if project.name == name:
                    return project.id
        log.debug("Creating Todoist project %r (none found)", name)
        return self.api.add_project(name=name).id

    def get_active_tasks(self, project_id: str) -> list:
        tasks = []
        for page in self.api.get_tasks(project_id=project_id):
            tasks.extend(page)
        return tasks

    def get_task(self, task_id: str):
        try:
            return self.api.get_task(task_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.debug("Todoist task %s not found (404) — treating as deleted", task_id)
                return None
            raise

    def create_task(
        self,
        project_id: str,
        content: str,
        description: str,
        due_dt: datetime | None = None,
        all_day: bool = False,
    ) -> str:
        kwargs = {}
        if due_dt is not None:
            kwargs = {"due_date": due_dt.date()} if all_day else {"due_datetime": due_dt}
        log.debug("Creating Todoist task %r in project %s", content, project_id)
        return self.api.add_task(
            content=content, project_id=project_id, description=description, **kwargs
        ).id

    def set_task_due(self, task_id: str, due_dt: datetime, all_day: bool) -> None:
        log.debug("Setting Todoist task %s due date to %s", task_id, due_dt)
        if all_day:
            self.api.update_task(task_id, due_date=due_dt.date())
        else:
            self.api.update_task(task_id, due_datetime=due_dt)

    def set_task_content(self, task_id: str, content: str) -> None:
        log.debug("Setting Todoist task %s title to %r", task_id, content)
        self.api.update_task(task_id, content=content)

    def set_task_description(self, task_id: str, description: str) -> None:
        log.debug("Setting Todoist task %s description", task_id)
        self.api.update_task(task_id, description=description)

    def complete_task(self, task_id: str) -> None:
        log.debug("Completing Todoist task %s", task_id)
        self.api.complete_task(task_id)

    def uncomplete_task(self, task_id: str) -> None:
        log.debug("Uncompleting Todoist task %s", task_id)
        self.api.uncomplete_task(task_id)
