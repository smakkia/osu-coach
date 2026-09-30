"""Entry point of the packaged app (osu-coach.exe, built by tools/build-setup.ps1): the window when started with no
arguments, else the same commands as `python -m osu_coach`."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # the replay readers run in worker processes: this exe started again
    if len(sys.argv) > 2 and sys.argv[1] == "--pick-folder":
        from osu_coach.gui import pick_folder_here
        pick_folder_here(sys.argv[2])
        sys.exit()
    if len(sys.argv) == 1:
        sys.argv.append("ui")
    from osu_coach.__main__ import main
    main()
