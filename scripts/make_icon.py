import os
import sys

project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project)

from tg.tray import make_icon_image

out = os.path.join(project, "assets", "icon.ico")
os.makedirs(os.path.dirname(out), exist_ok=True)
make_icon_image(256).save(
    out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
print("icon written to", out)