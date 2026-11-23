"""Fill a video's scenes with stand-in images and clips, so the render steps can be tested
without Midjourney or Kling. Usage: python tests/fake_assets.py <slug> [--clips]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidpipe.config import Video
from vidpipe.selftest import fake_clip, fake_image
from vidpipe.util import read_json


def main():
    v = Video(sys.argv[1])
    shot = read_json(v.shotlist)
    n_img = n_clip = 0
    for k, sc in enumerate(shot["scenes"]):
        if sc["type"] not in ("still", "motion"):
            continue
        img = v.assets("images") / f"{sc['id']}.png"
        if not img.exists():
            fake_image(sc, img, k)
            n_img += 1
        if sc["type"] == "motion" and "--clips" in sys.argv:
            clip = v.assets("clips") / f"{sc['id']}.mp4"
            if not clip.exists():
                fake_clip(img, clip)
                n_clip += 1
    print(f"made {n_img} stand-in images and {n_clip} stand-in clips")


if __name__ == "__main__":
    main()
