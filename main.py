#!/usr/bin/python

import argparse
import os
import sqlite3
from datetime import datetime, timedelta
import time
from shutil import copyfile
from sqlite3 import Error
import sys
import tty
import termios
import subprocess

def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        print("Database Error: {}".format(e))
    return conn

def get_all_memos(conn):
    cur = conn.cursor()
    # ZCUSTOMLABEL is the title you gave it, ZPATH is the filename
    cur.execute("SELECT ZDATE, ZDURATION, ZCUSTOMLABEL, ZPATH FROM ZCLOUDRECORDING ORDER BY ZDATE")
    return cur.fetchall()

def main():
    _db_path_default = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                                    "com.apple.voicememos", "Recordings", "CloudRecordings.db")
    _export_path_default = os.path.join(os.path.expanduser("~"), "Voice Memos Export")

    parser = argparse.ArgumentParser(description='Export Voice Memos from macOS 10.15 with Error Logging.')
    parser.add_argument("-d", "--db_path", type=str, help="path to database", default=_db_path_default)
    parser.add_argument("-e", "--export_path", type=str, help="path for exportation", default=_export_path_default)
    parser.add_argument("-a", "--all", action="store_true", help="export all at once")
    parser.add_argument("--date_in_name", action="store_true", help="include date in file name")
    parser.add_argument("--date_in_name_format", type=str, help="date format", default="%Y-%m-%d-%H-%M-%S_")
    parser.add_argument("--no_finder", action="store_true", help="don't open finder")
    args = parser.parse_args()

    _cols = [{"n": "Date", "w": 19}, {"n": "Duration", "w": 11}, {"n": "Old Path", "w": 32}, 
             {"n": "New Path", "w": 60}, {"n": "Status", "w": 15}]

    _dt_offset = 978307200.825232

    def getWidth(name):
        for c in _cols:
            if c["n"] == name: return c["w"]
        return False

    def helper_str(seperator):
        return seperator.join(["{" + str(i) + ":" + str(c["w"]) + "}" for i, c in enumerate(_cols)])

    def body_row(content_list):
        return "│ " + helper_str(" │ ").format(*content_list) + " │"

    # Permission check
    if not os.access(args.db_path, os.R_OK):
        print("CRITICAL ERROR: No permission to read the database.")
        print("Please grant Terminal 'Full Disk Access' in System Preferences.")
        exit()

    conn = create_connection(args.db_path)
    if not conn: exit()
    with conn:
        rows = get_all_memos(conn)
    if not rows: exit()

    if not os.path.exists(args.export_path):
        os.makedirs(args.export_path)

    # Initialize Log File
    log_path = os.path.join(args.export_path, "failed_exports.txt")
    with open(log_path, "w") as log_file:
        log_file.write("Voice Memos Export Log - {}\n".format(datetime.now()))
        log_file.write("="*50 + "\n")

    print("\n┌─" + helper_str("─┬─").format(*["─" * c["w"] for c in _cols]) + "─┐")
    print("│ " + helper_str(" │ ").format(*[c["n"] for c in _cols]) + " │")
    print("├─" + helper_str("─┼─").format(*["─" * c["w"] for c in _cols]) + "─┤")

    failed_count = 0
    success_count = 0

    for row in rows:
        date = datetime.fromtimestamp(row[0] + _dt_offset)
        date_str = date.strftime("%d.%m.%Y %H:%M:%S")
        duration_str = str(timedelta(seconds=row[1])).split('.')[0]
        
        # Clean the label for filenames
        label = row[2].encode('ascii', 'ignore').decode("ascii").replace("/", "_") if row[2] else "Untitled"
        path_old_raw = row[3] if row[3] else ""
        
        if path_old_raw:
            # Reconstruct absolute path
            if not path_old_raw.startswith("/"):
                path_old = os.path.join(os.path.dirname(args.db_path), path_old_raw)
            else:
                path_old = path_old_raw
                
            path_new = label + os.path.splitext(path_old)[1]
            path_new = date.strftime(args.date_in_name_format) + path_new if args.date_in_name else path_new
            path_new = os.path.join(args.export_path, path_new)
        else:
            path_old = ""
            path_new = ""

        p_old_short = ("..." + path_old[-(getWidth("Old Path")-3):]) if len(path_old) > getWidth("Old Path") else path_old
        p_new_short = ("..." + path_new[-(getWidth("New Path")-3):]) if len(path_new) > getWidth("New Path") else path_new

        # INTERACTIVE CHECK
        if args.all:
            key = 10
        else:
            print(body_row((date_str, duration_str, p_old_short, p_new_short, "Export?")), end="\r")
            fd = sys.stdin.fileno()
            old_set = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                key = ord(sys.stdin.read(1))
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_set)

        if key == 10: # User chose to export
            try:
                if not path_old or not os.path.exists(path_old):
                    raise FileNotFoundError("Audio file not found on disk (likely in iCloud)")
                
                copyfile(path_old, path_new)
                mod_time = time.mktime(date.timetuple())
                os.utime(path_new, (mod_time, mod_time))
                print(body_row((date_str, duration_str, p_old_short, p_new_short, "Success!")))
                success_count += 1

            except Exception as e:
                # LOG THE FAILURE AND CONTINUE
                with open(log_path, "a") as log_file:
                    log_file.write("FAILED: {} | Memo: {} | Reason: {}\n".format(date_str, label, str(e)))
                print(body_row((date_str, duration_str, p_old_short, p_new_short, "FAILED (Logged)")))
                failed_count += 1

        elif key == 27: # ESC
            print(body_row((date_str, duration_str, p_old_short, p_new_short, "Skipped")))

    print("└─" + helper_str("─┴─").format(*["─" * c["w"] for c in _cols]) + "─┘")
    print("\n--- SUMMARY ---")
    print("Successfully Exported: {}".format(success_count))
    print("Failed/Inconsistent:   {}".format(failed_count))
    print("Log file saved at:     {}".format(log_path))
    print("\nDone. Check the folder: {}".format(args.export_path))

    if not args.no_finder:
        subprocess.Popen(["open", args.export_path])

if __name__ == '__main__':
    main()
