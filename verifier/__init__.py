"""The deterministic verifier: each tool's checks live in its own module, on contract instances."""

from contract import Task, Tool

from . import daily, dates, pin, report, task

TOOLS = {
    Tool.PIN_ALERTS: pin,
    Tool.GET_DAILY: daily,
    Tool.REPORT_ALERTS: report,
    Tool.CHECK_DATE: dates,
}
# task -> its verdict, verify_task(arguments, output, reference, workspace)
TASKS = {Task.DAILY_ALERT: task.verify_task, Task.CHECK_DATE: dates.verify_task}


def verify_input(tool, value):
    return TOOLS[Tool(tool)].verify_input(value)


def verify_output(tool, input_value, output_value, workspace):
    return TOOLS[Tool(tool)].verify_output(input_value, output_value, workspace)


def verify_task(task, arguments, output, reference, workspace):
    return TASKS[Task(task)](arguments, output, reference, workspace)
