import sys
import tkinter as tk

from tg.app import TaskbarGlassApp
from tg.single_instance import acquire as acquire_single_instance


def _notify_already_running():
    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.configure(bg="#22242a")
        root.attributes("-topmost", True)
        label = tk.Label(
            root,
            text="Translucent momo is already running.\nCheck your system tray.",
            bg="#22242a",
            fg="#e6e6e6",
            font=("Segoe UI", 10),
            padx=22,
            pady=16,
        )
        label.pack()
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() // 2) - h - 60
        root.geometry("+%d+%d" % (x, y))
        root.after(3000, root.destroy)
        root.mainloop()
    except Exception:
        pass


def main():
    if not acquire_single_instance():
        _notify_already_running()
        return
    quit_after = 0
    if len(sys.argv) == 2 and sys.argv[1].startswith("--quit-after="):
        try:
            quit_after = int(sys.argv[1].split("=", 1)[1])
        except ValueError:
            pass
    TaskbarGlassApp().run(quit_after=quit_after)


if __name__ == "__main__":
    main()