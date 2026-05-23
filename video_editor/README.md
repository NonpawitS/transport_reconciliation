# video_editor

ตัวช่วยตัดต่อวิดีโอแบบ scriptable บน **MoviePy 2.x** (ต้องการ `ffmpeg` ในระบบ)

## Install

```bash
sudo apt-get install -y ffmpeg
pip install -r video_editor/requirements.txt
```

## CLI

รันจาก root ของ repo (โฟลเดอร์เดียวกับ `video_editor/`):

```bash
# 1) ตัดช่วงเวลา 5–12 วินาที
python -m video_editor.cli trim input.mp4 out.mp4 --start 5 --end 12

# 2) ต่อหลายคลิป
python -m video_editor.cli concat a.mp4 b.mp4 c.mp4 --dst joined.mp4

# 3) ใส่ข้อความ/ซับ (ซ้ำ --overlay ได้)
python -m video_editor.cli text in.mp4 out.mp4 \
    --overlay 0:3:Intro \
    --overlay 4:8:"Second line"
```

รูปแบบ overlay: `START:END:TEXT` (หน่วยเป็นวินาที)

## Python API

```python
from video_editor import TextOverlay, add_text, concat, trim

trim("input.mp4", 5, 12, "clip.mp4")
concat(["a.mp4", "b.mp4"], "joined.mp4")
add_text(
    "joined.mp4",
    [TextOverlay(text="สวัสดี", start=0, end=3, fontsize=64)],
    "final.mp4",
)
```

`TextOverlay` รองรับ: `fontsize`, `color`, `stroke_color`, `stroke_width`,
`position` (`("center","bottom")`, `"center"`, …), และ `font` (path
ของไฟล์ `.ttf`; ค่า default คือ DejaVu Sans Bold)

## Notes

- `concat(..., method="compose")` คือค่า default — รองรับคลิปคนละ
  resolution. ใช้ `"chain"` เมื่อ resolution / fps ตรงกันทุกคลิป
  (เร็วกว่า)
- ฟอนต์ default ใช้ DejaVu Sans Bold ของระบบ Linux; ถ้าใช้ภาษาไทย
  แนะนำ pass `font=` ของไฟล์ `.ttf` ที่รองรับภาษาไทย (เช่น Noto Sans
  Thai)
