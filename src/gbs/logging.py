"""GBS Logging Infrastructure

Provides file-based logging with configurable verbosity.
All operations are logged to file for post-mortem analysis.

This module now bridges Python logging to the FeedbackHub system,
ensuring all log messages are captured in both traditional log files
and the structured UI message system.
"""

import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .ui.hub import FeedbackHub


class FeedbackHubHandler(logging.Handler):
    """Logging handler that forwards log records to FeedbackHub

    This bridges Python's logging system to the GBS UI message system,
    ensuring all log messages are captured in structured output.
    """

    def __init__(self, hub: 'FeedbackHub'):
        """Initialize handler

        Args:
            hub: FeedbackHub instance to emit messages to
        """
        super().__init__()
        self.hub = hub

    def emit(self, record: logging.LogRecord):
        """Convert log record to LogMessage and emit to hub

        Args:
            record: Python logging record
        """
        try:
            from .ui.messages import LogMessage, LogLevel

            # Map Python logging levels to our LogLevel enum
            level_map = {
                logging.DEBUG: LogLevel.DEBUG,
                logging.INFO: LogLevel.INFO,
                logging.WARNING: LogLevel.WARNING,
                logging.ERROR: LogLevel.ERROR,
                logging.CRITICAL: LogLevel.CRITICAL,
            }

            level = level_map.get(record.levelno, LogLevel.INFO)
            message = self.format(record)
            source = record.name

            # Emit LogMessage to hub
            self.hub.emit(LogMessage(
                level=level,
                message=message,
                source=source
            ))
        except Exception:
            # Don't let logging errors crash the application
            self.handleError(record)


class GBSLogger:
    """GBS logging system with file and console outputs"""

    def __init__(
        self,
        name: str = "gbs",
        log_dir: Optional[Path] = None,
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
    ):
        """Initialize GBS logger

        Args:
            name: Logger name
            log_dir: Directory for log files (default: gbs-build/logs in current directory)
            console_level: Logging level for console output
            file_level: Logging level for file output (always more verbose)
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)  # Capture everything
        self.logger.propagate = False

        # Clear any existing handlers
        self.logger.handlers.clear()

        # Set up log directory
        if log_dir is None:
            log_dir = Path.cwd() / "gbs-build" / "logs"
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"gbs_{timestamp}.log"
        self.log_file = log_file

        # Create/update symlink to latest log file
        latest_link = self.log_dir / "latest.log"
        try:
            # Remove existing symlink if present
            if latest_link.exists() or latest_link.is_symlink():
                latest_link.unlink()
            # Create new symlink (relative path for portability)
            os.symlink(log_file.name, latest_link)
        except Exception:
            # Non-fatal: if symlink creation fails, continue anyway
            pass

        # File handler - logs everything
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(file_level)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

        # Console handler - logs according to verbosity setting
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(console_level)
        console_formatter = logging.Formatter(
            "%(levelname)s: %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        self.logger.info(f"GBS logging initialized. Log file: {log_file}")

    def set_console_level(self, level: int):
        """Change console logging level

        Args:
            level: New logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        for handler in self.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stderr:
                handler.setLevel(level)

    def get_logger(self) -> logging.Logger:
        """Get the underlying logger instance

        Returns:
            The configured logger
        """
        return self.logger

    def attach_feedback_hub(self, hub: 'FeedbackHub'):
        """Attach a FeedbackHub handler to forward logs to UI system

        This bridges Python logging to the FeedbackHub, ensuring all log
        messages are captured in structured output.

        Args:
            hub: FeedbackHub instance to forward logs to
        """
        handler = FeedbackHubHandler(hub)
        handler.setLevel(logging.DEBUG)  # Let hub backends filter by level
        # Use simple formatter since FeedbackHub will handle formatting
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)

    def cleanup_old_logs(self, max_log_count: int) -> None:
        """Remove old log files, keeping only the most recent ones

        Args:
            max_log_count: Maximum number of log files to keep.
                           If 0, keep all logs (no cleanup).
        """
        if max_log_count == 0:
            self.logger.debug("Log cleanup disabled (max_log_count=0)")
            return

        # Find all log files (excluding symlinks like latest.log)
        log_files = []
        for f in self.log_dir.iterdir():
            if f.is_file() and not f.is_symlink() and f.suffix == ".log":
                log_files.append(f)

        if len(log_files) <= max_log_count:
            self.logger.debug(f"Log cleanup: {len(log_files)} logs, keeping {max_log_count}, nothing to remove")
            return

        # Sort by modification time (newest first)
        log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        # Remove old logs
        logs_to_remove = log_files[max_log_count:]
        for log_file in logs_to_remove:
            try:
                log_file.unlink()
                self.logger.debug(f"Removed old log: {log_file.name}")
            except Exception as e:
                self.logger.warning(f"Failed to remove old log {log_file}: {e}")


# Global logger instance
_logger_instance: Optional[GBSLogger] = None


def setup_logging(
    verbose: bool = False,
    debug: bool = False,
    log_dir: Optional[Path] = None,
) -> GBSLogger:
    """Set up global GBS logging

    Args:
        verbose: Enable verbose console output (INFO level)
        debug: Enable debug console output (DEBUG level)
        log_dir: Custom log directory

    Returns:
        Configured GBSLogger instance
    """
    global _logger_instance

    # Determine console level
    if debug:
        console_level = logging.DEBUG
    elif verbose:
        console_level = logging.INFO
    else:
        console_level = logging.FATAL

    _logger_instance = GBSLogger(
        name="gbs",
        log_dir=log_dir,
        console_level=console_level,
        file_level=logging.DEBUG,
    )

    return _logger_instance


def get_logger(name: str = "gbs") -> logging.Logger:
    """Get a logger instance

    Args:
        name: Logger name (will be prefixed with 'gbs.')

    Returns:
        Logger instance
    """
    if name == "gbs":
        if _logger_instance is None:
            # Auto-initialize with defaults if not set up
            setup_logging()
        return _logger_instance.logger

    # Child logger: return by-name reference; the "gbs" root logger will
    # be configured by setup_logging() and child records propagate up.
    # Not auto-initializing here avoids creating a stray log file when
    # modules do `logger = get_logger(__name__)` at import time before
    # the CLI has processed -C/--directory.
    return logging.getLogger(f"gbs.{name}")


def get_log_file() -> Optional[Path]:
    """Get the current log file path

    Returns:
        Path to current log file, or None if logging not initialized
    """
    if _logger_instance is None:
        return None
    return _logger_instance.log_file
