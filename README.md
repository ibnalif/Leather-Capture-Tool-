# Leather Grading Dataset Capture Tool

A desktop tool for collecting and annotating leather images for quality grading and defect-segmentation datasets.

The application displays a live camera feed, captures a frame, lets an operator mark defective regions, and saves the original image, a binary defect mask, and descriptive metadata as one dataset sample.

## Features

- Live feed from a USB/webcam camera or another OpenCV-compatible source
- Capture and freeze a frame for annotation
- Pen and eraser tools for drawing defect regions
- Adjustable brush size
- Zoom and pan for accurate annotation
- Undo and clear controls
- Configurable grades, leather size categories, defect types, and text fields
- Automatic dataset directory creation
- Keyboard shortcuts for common annotation actions

## Requirements

- Windows, macOS, or Linux
- Python 3
- A working camera, unless an existing video source is configured
- Tkinter (usually included with standard Python installations)

Python packages:

- OpenCV
- NumPy
- Pillow

## Installation

1. Clone or download this repository.
2. Open a terminal in the project directory.
3. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install the required packages:

   ```powershell
   python -m pip install opencv-python numpy Pillow
   ```

## Running the application

Run the capture tool from its directory:

```powershell
cd grader_tool
python leather_capture_tool.py
```

On startup, the tool attempts to open the camera configured in `grader_tool/config.json`. If no camera source is present, it defaults to camera index `0`.

### Selecting a camera

Set `camera_source` in `grader_tool/config.json` to:

- `"0"`, `"1"`, or another camera index
- `"auto"` to probe common camera indexes
- A video stream URL supported by OpenCV

You can also override the configuration for one session with the `LEATHER_CAMERA_SOURCE` environment variable:

```powershell
$env:LEATHER_CAMERA_SOURCE = "1"
python leather_capture_tool.py
```

For an Android phone, enable USB webcam mode so Windows exposes the phone as a camera device, then select its camera index.

## Annotation workflow

1. Start the application and confirm that the live feed is visible.
2. Select a grade and leather size category.
3. Press **Capture Frame** or the **Space** key.
4. Use the red pen to mark defective areas.
5. Use the blue eraser to remove unwanted marks.
6. Add defect types, notes, and any configured additional fields.
7. Use **Save Sample**.
8. The tool automatically returns to the live feed for the next sample.

### Controls

| Action | Control |
| --- | --- |
| Capture frame | `Space` |
| Undo last stroke | `Ctrl+Z` |
| Select pen | `P` |
| Select eraser | `E` |
| Zoom | Mouse wheel or **Zoom + / Zoom -** |
| Pan | Middle- or right-drag on the canvas |
| Reset view | **Reset View** |

## Dataset output

Saved samples are written under `grader_tool/dataset/`:

```text
dataset/
├── images/
│   └── sample_YYYYMMDD_HHMMSS_microseconds.jpg
├── masks/
│   └── sample_YYYYMMDD_HHMMSS_microseconds_mask.png
└── metadata/
    └── sample_YYYYMMDD_HHMMSS_microseconds.json
```

The image is the original captured camera frame. The mask is a single-channel binary PNG:

- `0` (black): usable/background region
- `255` (white): marked defect region

Each metadata file includes the image and mask paths, timestamp, grade, size category, selected defect types, notes, and configured text fields such as thickness, operator name, and batch ID.

## Configuration

Edit [`grader_tool/config.json`](grader_tool/config.json) to change the options shown in the application. The following fields are supported:

```json
{
  "grades": ["A", "B", "C", "D", "Waste"],
  "size_categories": ["5ft", "6ft", "7ft", "8ft", "9ft", "10ft", "11ft", "12ft"],
  "defect_types": ["Vein Marks", "Lamp Skin", "Spots", "Scars", "Wrinkles", "Holes"],
  "text_fields": ["thickness_mm", "operator_name", "batch_id"]
}
```

Restart the application after changing the configuration. Missing configuration fields are filled with the built-in defaults.

## Project structure

```text
grader_tool/
├── leather_capture_tool.py   # Main capture and annotation application
├── Cameratest.py              # Optional MJPEG camera connectivity test
├── config.json                # Application options
└── dataset/                   # Generated images, masks, and metadata

Public_Datasets/               # Supporting public datasets and labels
Papers/                        # Research papers used during the project
```

## Troubleshooting

### Camera unavailable

- Confirm that the camera is connected and not being used by another application.
- Try a different camera index, such as `"1"`.
- Set `"camera_source": "auto"` to probe multiple indexes.
- On Windows, check that camera permissions are enabled for desktop applications.

### Tkinter is missing

Install Python from the official Python installer and enable the standard library/Tk option. On Linux, install the distribution package that provides Tkinter.

### No samples are saved

Make sure a frame has been captured before selecting **Save Sample** and confirm that the application has write permission for `grader_tool/dataset/`.

## Research materials

The repository includes papers and public datasets related to leather inspection, industrial defect detection, and computer-vision-based quality control. Check their individual licenses and terms before redistributing or using them outside this project.
