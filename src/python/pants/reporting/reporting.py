# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os
import sys
from io import BytesIO

from pants.base.workunit import WorkUnitLabel
from pants.reporting.html_reporter import HtmlReporter
from pants.reporting.plaintext_reporter import LabelFormat, PlainTextReporter, ToolOutputFormat
from pants.reporting.quiet_reporter import QuietReporter
from pants.reporting.report import Report
from pants.reporting.reporter import ReporterDestination
from pants.reporting.reporting_server import ReportingServerManager
from pants.subsystem.subsystem import Subsystem
from pants.util.dirutil import relative_symlink, safe_mkdir


class Reporting(Subsystem):
    options_scope = "reporting"

    @classmethod
    def register_options(cls, register):
        super().register_options(register)
        register(
            "--reports-dir",
            advanced=True,
            metavar="<dir>",
            default=os.path.join(register.bootstrap.pants_workdir, "reports"),
            help="Write reports to this dir.",
        )
        register(
            "--template-dir",
            advanced=True,
            metavar="<dir>",
            default=None,
            help="Find templates for rendering in this dir.",
        )
        register(
            "--console-label-format",
            advanced=True,
            type=dict,
            default=PlainTextReporter.LABEL_FORMATTING,
            help="Controls the printing of workunit labels to the console.  Workunit types are "
            "{workunits}.  Possible formatting values are {formats}".format(
                workunits=list(WorkUnitLabel.keys()), formats=list(LabelFormat.keys())
            ),
        )
        register(
            "--console-tool-output-format",
            advanced=True,
            type=dict,
            default=PlainTextReporter.TOOL_OUTPUT_FORMATTING,
            help="Controls the printing of workunit tool output to the console. Workunit types are "
            "{workunits}.  Possible formatting values are {formats}".format(
                workunits=list(WorkUnitLabel.keys()), formats=list(ToolOutputFormat.keys())
            ),
        )

    def initialize(self, run_tracker, all_options, start_time=None):
        """Initialize with the given RunTracker.

        TODO: See `RunTracker.start`.
        """

        run_id, run_uuid = run_tracker.initialize(all_options)
        run_dir = os.path.join(self.get_options().reports_dir, run_id)

        html_dir = os.path.join(run_dir, "html")
        safe_mkdir(html_dir)
        relative_symlink(run_dir, os.path.join(self.get_options().reports_dir, "latest"))

        report = Report()

        # Capture initial console reporting into a buffer. We'll do something with it once
        # we know what the cmd-line flag settings are.
        outfile = BytesIO()
        errfile = BytesIO()
        capturing_reporter_settings = PlainTextReporter.Settings(
            outfile=outfile,
            errfile=errfile,
            log_level=Report.INFO,
            color=False,
            indent=True,
            timing=False,
            label_format=self.get_options().console_label_format,
            tool_output_format=self.get_options().console_tool_output_format,
        )
        capturing_reporter = PlainTextReporter(run_tracker, capturing_reporter_settings)
        report.add_reporter("capturing", capturing_reporter)

        # Set up HTML reporting. We always want that.
        html_reporter_settings = HtmlReporter.Settings(
            log_level=Report.INFO, html_dir=html_dir, template_dir=self.get_options().template_dir
        )
        html_reporter = HtmlReporter(run_tracker, html_reporter_settings)
        report.add_reporter("html", html_reporter)

        # Add some useful RunInfo.
        run_tracker.run_info.add_info("default_report", html_reporter.report_path())
        port = ReportingServerManager().socket
        if port:
            run_tracker.run_info.add_info(
                "report_url", "http://localhost:{}/run/{}".format(port, run_id)
            )

        # And start tracking the run.
        run_tracker.start(report, start_time)

    @staticmethod
    def _consume_stringio(f):
        f.flush()
        buffered_output = f.getvalue()
        f.close()
        return buffered_output

    def update_reporting(self, global_options, is_quiet, run_tracker):
        """Updates reporting config once we've parsed cmd-line flags."""

        # Get any output silently buffered in the old console reporter, and remove it.
        removed_reporter = run_tracker.report.remove_reporter("capturing")
        buffered_out = self._consume_stringio(removed_reporter.settings.outfile)
        buffered_err = self._consume_stringio(removed_reporter.settings.errfile)

        log_level = Report.report_level_from_log_level(global_options.level)
        # Ideally, we'd use terminfo or somesuch to discover whether a
        # terminal truly supports color, but most that don't set TERM=dumb.
        color = global_options.colors and (os.getenv("TERM") != "dumb")
        timing = global_options.time

        if is_quiet:
            console_reporter = QuietReporter(
                run_tracker,
                QuietReporter.Settings(log_level=log_level, color=color, timing=timing),
            )
        else:
            # Set up the new console reporter.
            stdout = sys.stdout.buffer
            stderr = sys.stderr.buffer
            settings = PlainTextReporter.Settings(
                log_level=log_level,
                outfile=stdout,
                errfile=stderr,
                color=color,
                indent=True,
                timing=timing,
                label_format=self.get_options().console_label_format,
                tool_output_format=self.get_options().console_tool_output_format,
            )
            console_reporter = PlainTextReporter(run_tracker, settings)
            console_reporter.emit(buffered_out, dest=ReporterDestination.OUT)
            console_reporter.emit(buffered_err, dest=ReporterDestination.ERR)
            console_reporter.flush()
        run_tracker.report.add_reporter("console", console_reporter)

        if global_options.logdir:
            # Also write plaintext logs to a file. This is completely separate from the html reports.
            safe_mkdir(global_options.logdir)
            run_id = run_tracker.run_info.get_info("id")
            outfile = open(os.path.join(global_options.logdir, "{}.log".format(run_id)), "wb")
            errfile = open(os.path.join(global_options.logdir, "{}.err.log".format(run_id)), "wb")
            settings = PlainTextReporter.Settings(
                log_level=log_level,
                outfile=outfile,
                errfile=errfile,
                color=False,
                indent=True,
                timing=True,
                label_format=self.get_options().console_label_format,
                tool_output_format=self.get_options().console_tool_output_format,
            )
            logfile_reporter = PlainTextReporter(run_tracker, settings)
            logfile_reporter.emit(buffered_out, dest=ReporterDestination.OUT)
            logfile_reporter.emit(buffered_err, dest=ReporterDestination.ERR)
            logfile_reporter.flush()
            run_tracker.report.add_reporter("logfile", logfile_reporter)


def is_hex_string(id_value):
    return all(is_hex_ch(ch) for ch in id_value)


def is_hex_ch(ch):
    num = ord(ch)
    return ord("0") <= num <= ord("9") or ord("a") <= num <= ord("f") or ord("A") <= num <= ord("F")
