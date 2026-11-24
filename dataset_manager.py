import os
import json
import csv
import shutil
from datetime import datetime
from PIL import Image

KNOWN_DEFECTS = [
    "scratch",
    "hole",
    "discoloration",
    "scar",
    "wrinkle",
    "vein_marks",
    "lamp_skin",
    "spots",
    "color_variation"
]

def clean_tag(tag):
    return tag.lower().strip().replace(" ", "_").replace("-", "_")

def get_image_info(filepath):
    try:
        with Image.open(filepath) as img:
            w, h = img.size
            return w, h, round(w / max(h, 1), 3)
    except Exception:
        return 0, 0, 0.0

def sync_dataset_to_kaggle_format(dataset_root):
    """
    Scans the dataset directory, ensures centralized 'images/' and 'masks/'
    folders are populated, and generates comprehensive 'metadata.csv' and
    'pieces_summary.csv' tailored for Kaggle EDA and ML pipelines.
    """
    dataset_root = os.path.abspath(dataset_root)
    if not os.path.isdir(dataset_root):
        os.makedirs(dataset_root, exist_ok=True)

    images_dir = os.path.join(dataset_root, "images")
    masks_dir = os.path.join(dataset_root, "masks")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(masks_dir, exist_ok=True)

    # Find all piece folders (folders starting with Piece_ or containing metadata.json)
    piece_dirs = []
    for entry in os.listdir(dataset_root):
        full_p = os.path.join(dataset_root, entry)
        if os.path.isdir(full_p) and entry not in ("images", "masks", "metadata", ".git"):
            if os.path.exists(os.path.join(full_p, "metadata.json")) or entry.startswith("Piece_"):
                piece_dirs.append(full_p)

    piece_dirs.sort()

    samples_rows = []
    pieces_rows = []

    for p_dir in piece_dirs:
        piece_id = os.path.basename(p_dir)
        meta_file = os.path.join(p_dir, "metadata.json")
        meta = {}
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass

        grade = meta.get("grade", "A")
        size_cat = meta.get("size_category") or meta.get("size", "Medium")
        raw_defects = meta.get("defect_types_present") or meta.get("defects", [])
        notes = meta.get("notes", "")
        received_at = meta.get("received_at") or meta.get("timestamp", datetime.now().isoformat())

        cleaned_defects = [d.strip() for d in raw_defects if str(d).strip()]
        has_defect = 1 if len(cleaned_defects) > 0 else 0
        defect_count = len(cleaned_defects)
        defects_str = ";".join(cleaned_defects)

        defect_flags = {}
        for kd in KNOWN_DEFECTS:
            defect_flags[f"defect_{kd}"] = 0
        defect_flags["defect_other"] = 0

        for d in cleaned_defects:
            c = clean_tag(d)
            matched = False
            for kd in KNOWN_DEFECTS:
                if kd in c:
                    defect_flags[f"defect_{kd}"] = 1
                    matched = True
            if not matched:
                defect_flags["defect_other"] = 1

        # Check front and back files
        sides = [
            ("front", "Grain", "front.jpg", "front_mask.png"),
            ("back", "Suede", "back.jpg", "back_mask.png")
        ]

        piece_summary = {
            "piece_id": piece_id,
            "grade": grade,
            "size_category": size_cat,
            "has_defect": has_defect,
            "defect_count": defect_count,
            "defects": defects_str,
            "front_image": "",
            "back_image": "",
            "front_mask": "",
            "back_mask": "",
            "front_size_kb": 0.0,
            "back_size_kb": 0.0,
            "notes": notes,
            "timestamp": received_at
        }
        piece_summary.update(defect_flags)

        for side_key, surface_label, raw_img_name, raw_mask_name in sides:
            src_img = os.path.join(p_dir, raw_img_name)
            src_mask = os.path.join(p_dir, raw_mask_name)

            if not os.path.exists(src_img):
                continue

            target_img_name = f"{piece_id}_{side_key}.jpg"
            dest_img = os.path.join(images_dir, target_img_name)
            
            # Sync image file if missing or modified
            if not os.path.exists(dest_img) or os.path.getsize(dest_img) != os.path.getsize(src_img):
                try:
                    shutil.copy2(src_img, dest_img)
                except Exception:
                    pass

            target_mask_name = f"{piece_id}_{side_key}_mask.png"
            dest_mask = os.path.join(masks_dir, target_mask_name)
            has_mask = 0
            if os.path.exists(src_mask):
                if not os.path.exists(dest_mask) or os.path.getsize(dest_mask) != os.path.getsize(src_mask):
                    try:
                        shutil.copy2(src_mask, dest_mask)
                    except Exception:
                        pass
                has_mask = 1

            # Relative paths for portable Kaggle notebooks
            rel_img = f"images/{target_img_name}"
            rel_mask = f"masks/{target_mask_name}" if has_mask else ""

            w, h, ar = get_image_info(src_img)
            fsize_kb = round(os.path.getsize(src_img) / 1024.0, 1)

            sample_row = {
                "sample_id": f"{piece_id}_{side_key}",
                "piece_id": piece_id,
                "side": side_key,
                "surface_type": surface_label,
                "image_path": rel_img,
                "mask_path": rel_mask,
                "has_mask": has_mask,
                "grade": grade,
                "size_category": size_cat,
                "has_defect": has_defect,
                "defect_count": defect_count,
                "defects": defects_str,
                "image_width": w,
                "image_height": h,
                "aspect_ratio": ar,
                "file_size_kb": fsize_kb,
                "notes": notes,
                "timestamp": received_at
            }
            sample_row.update(defect_flags)
            samples_rows.append(sample_row)

            if side_key == "front":
                piece_summary["front_image"] = rel_img
                piece_summary["front_mask"] = rel_mask
                piece_summary["front_size_kb"] = fsize_kb
            else:
                piece_summary["back_image"] = rel_img
                piece_summary["back_mask"] = rel_mask
                piece_summary["back_size_kb"] = fsize_kb

        pieces_rows.append(piece_summary)

    # Write metadata.csv (Primary sample table for Kaggle EDA / PyTorch Dataset)
    if samples_rows:
        meta_csv_path = os.path.join(dataset_root, "metadata.csv")
        headers = list(samples_rows[0].keys())
        with open(meta_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(samples_rows)
        print(f"[DATASET SYNC] Updated {meta_csv_path} ({len(samples_rows)} samples)")

    # Write pieces_summary.csv (Paired Hide table)
    if pieces_rows:
        summary_csv_path = os.path.join(dataset_root, "pieces_summary.csv")
        headers = list(pieces_rows[0].keys())
        with open(summary_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(pieces_rows)
        print(f"[DATASET SYNC] Updated {summary_csv_path} ({len(pieces_rows)} pieces)")

    return len(samples_rows), len(pieces_rows)

if __name__ == "__main__":
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
    samples, pieces = sync_dataset_to_kaggle_format(test_dir)
    print(f"Sync complete: {samples} samples across {pieces} pieces.")
