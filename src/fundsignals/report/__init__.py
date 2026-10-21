"""Weekly deliverable: md/HTML/CSV/JSON report files plus optional email and Slack delivery."""

from fundsignals.report.delivery import deliver
from fundsignals.report.writer import why_line, write_report

__all__ = ["deliver", "why_line", "write_report"]
