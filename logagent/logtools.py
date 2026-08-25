"""
Log tools for the agent harness.

Each tool operates on log files under a configured log root.
"""

import os
import re
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import heapq


def _resolve_path(log_root: str, path: str) -> str:
    """Resolve path and ensure it's within log_root."""
    # Join and normalize
    full_path = os.path.normpath(os.path.join(log_root, path))
    # Ensure it's inside log_root
    if not full_path.startswith(os.path.normpath(log_root)):
        raise ValueError(f"Path '{path}' is outside log root '{log_root}'")
    return full_path


def _clamp_output(text: str, max_chars: int) -> Tuple[str, bool, int]:
    """Clamp text to max_chars, return (clamped_text, was_truncated, truncated_chars)."""
    if len(text) <= max_chars:
        return text, False, 0
    clamped = text[:max_chars]
    # Try to clamp at a newline to avoid cutting lines
    last_newline = clamped.rfind('\n')
    if last_newline > max_chars * 0.8:  # if we can keep at least 80% and cut at newline
        clamped = text[:last_newline]
    truncated = len(text) - len(clamped)
    return clamped, True, truncated


class LogTools:
    def __init__(self, log_root: str, output_char_budget: int = 500):
        self.log_root = log_root
        self.output_char_budget = output_char_budget
        # Ensure log root exists
        os.makedirs(self.log_root, exist_ok=True)

    def list_logs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        List log files in the log root.
        Args: {pattern: str*}  // glob pattern relative to log_root, default "*"
        Returns: list of log file paths (relative to log_root) and sizes.
        """
        pattern = args.get("pattern", "*")
        try:
            # Resolve pattern to ensure it stays within log_root
            # We'll glob inside log_root with the pattern
            search_path = os.path.join(self.log_root, pattern)
            # Security: ensure the search_path doesn't escape via ../ in pattern?
            # Our glob is rooted at log_root, so pattern starting with ../ would still be inside?
            # Actually, we joined with log_root, so if pattern contains ../, it could go outside.
            # We'll check each matched path.
            matches = glob.glob(search_path)
            files = []
            for match in matches:
                if os.path.isfile(match):
                    rel_path = os.path.relpath(match, self.log_root)
                    # Double-check
                    _resolve_path(self.log_root, rel_path)
                    size = os.path.getsize(match)
                    files.append({"path": rel_path, "size": size})
            # Sort by name
            files.sort(key=lambda x: x["path"])
            # Format output
            lines = [f"{f['path']} ({f['size']} bytes)" for f in files]
            output = "\n".join(lines) if lines else "No logs found"
            clamped, truncated, truncated_chars = _clamp_output(
                output, self.output_char_budget
            )
            if truncated:
                clamped += f"\n... {len(files)} matches, showing {len(lines)} lines, {truncated_chars} chars not shown"
            return {
                "content": [{"type": "text", "text": clamped}],
                "is_error": False,
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error listing logs: {str(e)}"}],
                "is_error": True,
            }

    def search_logs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search log files with regex.
        Args:
          - pattern: str (required)  // regex pattern
          - files: List[str]*        // list of log files to search (relative to log_root), default all
          - context_lines: int*      // lines of context around match (default 0)
          - limit: int*              // max number of matches to return (default 100)
        Returns: list of matches with file, line number, line text, and context.
        """
        pattern = args.get("pattern")
        if not pattern:
            return {
                "content": [{"type": "text", "text": "Error: 'pattern' is required"}],
                "is_error": True,
            }
        files_arg = args.get("files")  # list of relative paths
        context_lines = args.get("context_lines", 0)
        limit = args.get("limit", 100)

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {
                "content": [{"type": "text", "text": f"Invalid regex: {str(e)}"}],
                "is_error": True,
            }

        # Determine which files to search
        if files_arg is None:
            # All log files
            file_paths = [
                os.path.join(self.log_root, f)
                for f in os.listdir(self.log_root)
                if os.path.isfile(os.path.join(self.log_root, f))
            ]
        else:
            file_paths = []
            for rel_path in files_arg:
                try:
                    full_path = _resolve_path(self.log_root, rel_path)
                    if os.path.isfile(full_path):
                        file_paths.append(full_path)
                    else:
                        return {
                            "content": [{"type": "text", "text": f"File not found: {rel_path}"}],
                            "is_error": True,
                        }
                except ValueError as e:
                    return {
                        "content": [{"type": "text", "text": str(e)}],
                        "is_error": True,
                    }

        matches = []
        for file_path in file_paths:
            try:
                with open(file_path, 'r') as f:
                    lines = f.readlines()
            except Exception as e:
                return {
                    "content": [{"type": "text", "text": f"Error reading {file_path}: {str(e)}"}],
                    "is_error": True,
                }

            for i, line in enumerate(lines, start=1):  # 1-indexed line numbers
                if regex.search(line):
                    # Collect context
                    start = max(0, i - context_lines - 1)
                    end = min(len(lines), i + context_lines)
                    context = lines[start:end]
                    matches.append({
                        "file": os.path.relpath(file_path, self.log_root),
                        "line": i,
                        "match": line.rstrip(),
                        "context": [l.rstrip() for l in context],
                    })
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break

        # Format output
        if not matches:
            output = "No matches found"
        else:
            out_lines = []
            for m in matches:
                out_lines.append(f"{m['file']}:{m['line']}:{m['match']}")
                for ctx_line in m['context']:
                    if ctx_line != m['match']:  # avoid duplicating the match line
                        out_lines.append(f"   {ctx_line}")
                out_lines.append("")  # blank line between matches
            output = "\n".join(out_lines).strip()

        clamped, truncated, truncated_chars = _clamp_output(
            output, self.output_char_budget
        )
        if truncated:
            clamped += f"\n... {len(matches)} matches shown, {len(matches)} total matches, {truncated_chars} chars not shown"
        return {
            "content": [{"type": "text", "text": clamped}],
            "is_error": False,
        }

    def read_log(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Read a log file with pagination by line range.
        Args:
          - file: str (required)  // log file path relative to log_root
          - start_line: int*       // 1-indexed start line (default 1)
          - end_line: int*         // 1-indexed end line (inclusive, default start_line + 99)
        Returns: lines of the file in the given range.
        """
        file_rel = args.get("file")
        if not file_rel:
            return {
                "content": [{"type": "text", "text": "Error: 'file' is required"}],
                "is_error": True,
            }
        try:
            file_path = _resolve_path(self.log_root, file_rel)
        except ValueError as e:
            return {
                "content": [{"type": "text", "text": str(e)}],
                "is_error": True,
            }

        if not os.path.isfile(file_path):
            return {
                "content": [{"type": "text", "text": f"File not found: {file_rel}"}],
                "is_error": True,
            }

        start_line = args.get("start_line", 1)
        end_line = args.get("end_line")
        if end_line is None:
            end_line = start_line + 99  # default 100 lines

        # Validate line numbers
        if start_line < 1:
            start_line = 1
        if end_line < start_line:
            end_line = start_line

        try:
            with open(file_path, 'r') as f:
                # Read lines efficiently: we could use linecache or iterate
                lines = []
                for i, line in enumerate(f, start=1):
                    if i > end_line:
                        break
                    if i >= start_line:
                        lines.append(line.rstrip())
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error reading file: {str(e)}"}],
                "is_error": True,
            }

        output = "\n".join(lines) if lines else "(empty)"
        clamped, truncated, truncated_chars = _clamp_output(
            output, self.output_char_budget
        )
        if truncated:
            total_lines = len(lines)
            shown_lines = len(lines) if not truncated else len(clamped.split('\n'))
            # Actually, we need to know how many lines we showed vs total
            # We'll compute total lines in the file? Not needed for message.
            clamped += f"\n... showing lines {start_line}-{start_line + shown_lines - 1}, truncated {truncated_chars} chars"
        return {
            "content": [{"type": "text", "text": clamped}],
            "is_error": False,
        }

    def log_stats(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute statistics on log files.
        Args:
          - files: List[str]*        // list of log files to analyze (relative to log_root), default all
        Returns: severity counts (assuming logs have levels like ERROR, WARN, INFO, DEBUG)
                 and errors-over-time histogram (hourly buckets for the last 24 hours).
        """
        files_arg = args.get("files")
        if files_arg is None:
            file_paths = [
                os.path.join(self.log_root, f)
                for f in os.listdir(self.log_root)
                if os.path.isfile(os.path.join(self.log_root, f))
            ]
        else:
            file_paths = []
            for rel_path in files_arg:
                try:
                    full_path = _resolve_path(self.log_root, rel_path)
                    if os.path.isfile(full_path):
                        file_paths.append(full_path)
                except ValueError:
                    pass  # ignore invalid files, could also error

        # We'll assume log lines have a timestamp at the start and a level
        # Example: 2026-08-24 10:30:00 ERROR Something went wrong
        # We'll use a regex to extract timestamp and level
        timestamp_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
        level_re = re.compile(r'\b(ERROR|WARN|INFO|DEBUG)\b')

        stats = {
            "ERROR": 0,
            "WARN": 0,
            "INFO": 0,
            "DEBUG": 0,
            "OTHER": 0,
        }
        # Histogram: hourly buckets for the last 24 hours (we'll use current time as reference)
        # We'll store counts per hour offset from now (negative for past)
        # For simplicity, we'll just count errors per hour in the log timestamps
        error_hourly = {}  # hour string (YYYY-MM-DD HH) -> count

        for file_path in file_paths:
            try:
                with open(file_path, 'r') as f:
                    for line in f:
                        line = line.rstrip()
                        # Extract timestamp
                        ts_match = timestamp_re.match(line)
                        if ts_match:
                            ts_str = ts_match.group(1)
                            try:
                                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                hour_key = ts.strftime("%Y-%m-%d %H")
                            except ValueError:
                                ts = None
                                hour_key = None
                        else:
                            ts = None
                            hour_key = None

                        # Extract level
                        level_match = level_re.search(line)
                        level = level_match.group(1) if level_match else "OTHER"
                        if level in stats:
                            stats[level] += 1
                        else:
                            stats["OTHER"] += 1

                        # If it's an error and we have a timestamp, count in histogram
                        if level == "ERROR" and hour_key:
                            error_hourly[hour_key] = error_hourly.get(hour_key, 0) + 1
            except Exception as e:
                return {
                    "content": [{"type": "text", "text": f"Error processing {file_path}: {str(e)}"}],
                    "is_error": True,
                }

        # Format output
        out_lines = ["Severity counts:"]
        for level, count in stats.items():
            out_lines.append(f"  {level}: {count}")
        out_lines.append("")
        out_lines.append("Errors per hour (last 24h):")
        if error_hourly:
            # Sort by hour
            for hour in sorted(error_hourly.keys()):
                out_lines.append(f"  {hour}: {error_hourly[hour]}")
        else:
            out_lines.append("  No errors found")
        output = "\n".join(out_lines)

        clamped, truncated, truncated_chars = _clamp_output(
            output, self.output_char_budget
        )
        if truncated:
            clamped += f"\n... truncated {truncated_chars} chars"
        return {
            "content": [{"type": "text", "text": clamped}],
            "is_error": False,
        }

    def timeline(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge several log files into one chronological view around a timestamp.
        Args:
          - files: List[str] (required)  // list of log files (relative to log_root)
          - around: str (required)       // timestamp string "YYYY-MM-DD HH:MM:SS"
          - window: str*                 // time window as "HH:MM:SS" or seconds, default "00:05:00" (5 minutes)
          - limit: int*                  // max number of lines to return (default 100)
        Returns: lines from all files sorted by timestamp, within the window around the given timestamp.
        """
        files_arg = args.get("files")
        around_str = args.get("around")
        window_str = args.get("window", "00:05:00")
        limit = args.get("limit", 100)

        if not files_arg or not isinstance(files_arg, list):
            return {
                "content": [{"type": "text", "text": "Error: 'files' must be a non-empty list"}],
                "is_error": True,
            }
        if not around_str:
            return {
                "content": [{"type": "text", "text": "Error: 'around' timestamp is required"}],
                "is_error": True,
            }

        try:
            around_ts = datetime.strptime(around_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {
                "content": [{"type": "text", "text": "Error: 'around' must be in format YYYY-MM-DD HH:MM:SS"}],
                "is_error": True,
            }

        # Parse window
        try:
            # Could be HH:MM:SS or just seconds
            if ':' in window_str:
                h, m, s = window_str.split(':')
                window_seconds = int(h) * 3600 + int(m) * 60 + int(s)
            else:
                window_seconds = int(window_str)
        except ValueError:
            return {
                "content": [{"type": "text", "text": "Error: 'window' must be HH:MM:SS or seconds"}],
                "is_error": True,
            }

        start_ts = around_ts - timedelta(seconds=window_seconds)
        end_ts = around_ts + timedelta(seconds=window_seconds)

        # Collect all log lines with timestamps
        timestamp_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
        entries = []  # (timestamp, file, line_number, line_text)

        for rel_file in files_arg:
            try:
                file_path = _resolve_path(self.log_root, rel_file)
            except ValueError as e:
                return {
                    "content": [{"type": "text", "text": str(e)}],
                    "is_error": True,
                }
            if not os.path.isfile(file_path):
                return {
                    "content": [{"type": "text", "text": f"File not found: {rel_file}"}],
                    "is_error": True,
                }

            try:
                with open(file_path, 'r') as f:
                    for line_num, line in enumerate(f, start=1):
                        line = line.rstrip()
                        match = timestamp_re.match(line)
                        if match:
                            ts_str = match.group(1)
                            try:
                                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                if start_ts <= ts <= end_ts:
                                    entries.append((ts, rel_file, line_num, line))
                            except ValueError:
                                pass  # skip lines with invalid timestamp
            except Exception as e:
                return {
                    "content": [{"type": "text", "text": f"Error reading {file_path}: {str(e)}"}],
                    "is_error": True,
                }

        # Sort by timestamp
        entries.sort(key=lambda x: x[0])

        # Apply limit
        if limit is not None:
            entries = entries[:limit]

        # Format output
        out_lines = []
        for ts, rel_file, line_num, line in entries:
            out_lines.append(f"{ts} {rel_file}:{line_num}:{line}")
        output = "\n".join(out_lines) if out_lines else "No entries in time window"

        clamped, truncated, truncated_chars = _clamp_output(
            output, self.output_char_budget
        )
        if truncated:
            clamped += f"\n... showing {len(entries)} entries, truncated {truncated_chars} chars"
        return {
            "content": [{"type": "text", "text": clamped}],
            "is_error": False,
        }