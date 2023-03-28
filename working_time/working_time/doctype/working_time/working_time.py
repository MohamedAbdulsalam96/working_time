# Copyright (c) 2023, ALYF GmbH and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import time_diff_in_seconds
from working_time.jira_client import JiraClient

HALF_DAY = 3.25
OVERTIME_FACTOR = 1.15
MAX_HALF_DAY = HALF_DAY * OVERTIME_FACTOR * 60 * 60
FIVE_MINUTES = 5 * 60
ONE_HOUR = 60 * 60


class WorkingTime(Document):
    def before_validate(self):
        self.remove_seconds()
        self.set_to_times()
        self.set_durations()

    def validate(self):
        for log in self.time_logs:
            if log.duration and log.duration < 0:
                frappe.throw(_("Time logs must be continuous"))

    def on_submit(self):
        self.create_attendance()
        self.create_timesheets()

    def remove_seconds(self):
        for log in self.time_logs:
            if log.from_time:
                log.from_time = f"{log.from_time[:-2]}00"

            if log.to_time:
                log.to_time = f"{log.to_time[:-2]}00"

    def set_to_times(self):
        for i in range(0, len(self.time_logs) - 1):
            self.time_logs[i].to_time = self.time_logs[i + 1].from_time

    def set_durations(self):
        self.break_time = 0
        self.working_time = 0

        for log in self.time_logs:
            if log.from_time and log.to_time:
                log.duration = time_diff_in_seconds(log.to_time, log.from_time)

            if log.duration:
                if log.is_break:
                    self.break_time += log.duration
                else:
                    self.working_time += log.duration

    def create_attendance(self):
        if not frappe.db.exists(
            "Attendance", {"employee": self.employee, "attendance_date": self.date}
        ):
            attendance = frappe.get_doc(
                {
                    "doctype": "Attendance",
                    "employee": self.employee,
                    "status": "Present"
                    if self.working_time > MAX_HALF_DAY
                    else "Half Day",
                    "attendance_date": self.date,
                    "working_time": self.name,
                }
            )
            attendance.flags.ignore_permissions = True
            attendance.save()
            attendance.submit()

    def create_timesheets(self):
        for log in self.time_logs:
            if log.duration and log.project:
                costing_rate = get_costing_rate(self.employee)
                hours = math.ceil(log.duration / FIVE_MINUTES) * FIVE_MINUTES / ONE_HOUR
                billing_hours = (
                    math.ceil(
                        log.duration * float(log.billable[:-1]) / 100 / FIVE_MINUTES
                    )
                    * FIVE_MINUTES
                    / ONE_HOUR
                )

                customer, billing_rate, jira_site = frappe.get_value(
                    "Project",
                    log.project,
                    ["customer", "billing_rate", "jira_site"],
                )

                frappe.get_doc(
                    {
                        "doctype": "Timesheet",
                        "time_logs": [
                            {
                                "is_billable": 1,
                                "project": log.project,
                                "activity_type": "Default",
                                "base_billing_rate": billing_rate,
                                "base_costing_rate": costing_rate,
                                "costing_rate": costing_rate,
                                "billing_rate": billing_rate,
                                "hours": hours,
                                "from_time": self.date,
                                "billing_hours": billing_hours,
                                "description": get_description(
                                    jira_site, log.key, log.note
                                ),
                                "jira_issue_url": get_jira_issue_url(
                                    jira_site, log.key
                                ),
                            }
                        ],
                        "parent_project": log.project,
                        "customer": customer,
                        "employee": self.employee,
                        "working_time": self.name,
                    }
                ).insert()


def get_costing_rate(employee):
    return frappe.get_value(
        "Activity Cost",
        {"activity_type": "Default", "employee": employee},
        "costing_rate",
    )


def get_jira_issue_url(jira_site, key):
    return f"https://{jira_site}/browse/{key}" if key else None


def get_description(jira_site, key, note):
    if key:
        description = f"{JiraClient(jira_site).get_issue_summary(key)} ({key})"

        if note:
            description += f":\n\n{note}"
    else:
        description = note or "-"

    return description
