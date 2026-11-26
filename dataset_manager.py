import os
import json
import csv
import shutil
import threading
from datetime import datetime
from PIL import Image

_METADATA_LOCK = threading.Lock()
METADATA_INDEX_FILENAME = "pieces_metadata.json"

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

def load_pieces_metadata(dataset_root):
    index_path = os.path.join(os.path.abspath(dataset_root), METADATA_INDEX_FILENAME)
    if not os.path.exists(index_path):
        return {}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_pieces_metadata(dataset_root, data):
    dataset_root = os.path.abspath(dataset_root)
    os.makedirs(dataset_root, exist_ok=True)
    index_path = os.path.join(dataset_root, METADATA_INDEX_FILENAME)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def upsert_piece_metadata(dataset_root, piece_id, updates):
    with _METADATA_LOCK:
        all_meta = load_pieces_metadata(dataset_root)
        existing = all_meta.get(piece_id, {})
        if not isinstance(existing, dict):
            existing = {}
        existing.update(updates)
        all_meta[piece_id] = existing
        save_pieces_metadata(dataset_root, all_meta)
        return existing

def _discover_piece_ids(dataset_root):
    images_dir = os.path.join(dataset_root, "images")
    piece_ids = set()
    if os.path.isdir(images_dir):
        for fname in os.listdir(images_dir):
            lower = fname.lower()
            if lower.endswith("_front.jpg"):
                piece_ids.add(fname[:-len("_front.jpg")])
            elif lower.endswith("_back.jpg"):
                piece_ids.add(fname[:-len("_back.jpg")])
    return sorted(piece_ids)

def count_saved_pieces(dataset_root):
    dataset_root = os.path.abspath(dataset_root)
    if not os.path.isdir(dataset_root):
        return 0
    piece_ids = set(_discover_piece_ids(dataset_root))
    for entry in os.listdir(dataset_root):
        full_p = os.path.join(dataset_root, entry)
        if os.path.isdir(full_p) and entry not in ("images", "masks", "metadata", ".git"):
            if os.path.exists(os.path.join(full_p, "metadata.json")) or entry.startswith("Piece_"):
                piece_ids.add(entry)
    return len(piece_ids)

def sync_dataset_to_kaggle_format(dataset_root):
    """
    Scans the dataset directory ('images/', 'masks/', and 'pieces_metadata.json',
    plus any legacy Piece_* folders if present) and generates 'metadata.csv' and
    'pieces_summary.csv' tailored for Kaggle EDA and ML pipelines.
    """
    dataset_root = os.path.abspath(dataset_root)
    if not os.path.isdir(dataset_root):
        os.makedirs(dataset_root, exist_ok=True)

    images_dir = os.path.join(dataset_root, "images")
    masks_dir = os.path.join(dataset_root, "masks")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(masks_dir, exist_ok=True)

    with _METADATA_LOCK:
        all_meta = load_pieces_metadata(dataset_root)
        meta_dirty = False

        # Backwards compatibility: import any legacy Piece_* folders if present
        for entry in sorted(os.listdir(dataset_root)):
            full_p = os.path.join(dataset_root, entry)
            if os.path.isdir(full_p) and entry not in ("images", "masks", "metadata", ".git"):
                if os.path.exists(os.path.join(full_p, "metadata.json")) or entry.startswith("Piece_"):
                    piece_id = entry
                    meta_file = os.path.join(full_p, "metadata.json")
                    if os.path.exists(meta_file) and piece_id not in all_meta:
                        try:
                            with open(meta_file, "r", encoding="utf-8") as f:
                                all_meta[piece_id] = json.load(f)
                            meta_dirty = True
                        except Exception:
                            pass

                    for side_key, raw_img_name, raw_mask_name in (
                        ("front", "front.jpg", "front_mask.png"),
                        ("back", "back.jpg", "back_mask.png"),
                    ):
                        src_img = os.path.join(full_p, raw_img_name)
                        dest_img = os.path.join(images_dir, f"{piece_id}_{side_key}.jpg")
                        if os.path.exists(src_img) and (not os.path.exists(dest_img) or os.path.getsize(dest_img) != os.path.getsize(src_img)):
                            try:
                                shutil.copy2(src_img, dest_img)
                            except Exception:
                                pass

                        src_mask = os.path.join(full_p, raw_mask_name)
                        dest_mask = os.path.join(masks_dir, f"{piece_id}_{side_key}_mask.png")
                        if os.path.exists(src_mask) and (not os.path.exists(dest_mask) or os.path.getsize(dest_mask) != os.path.getsize(src_mask)):
                            try:
                                shutil.copy2(src_mask, dest_mask)
                            except Exception:
                                pass

        if meta_dirty:
            save_pieces_metadata(dataset_root, all_meta)

    piece_ids = _discover_piece_ids(dataset_root)

    samples_rows = []
    pieces_rows = []

    for piece_id in piece_ids:
        meta = all_meta.get(piece_id, {})
        if not isinstance(meta, dict):
            meta = {}

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

        sides = [
            ("front", "Grain"),
            ("back", "Suede"),
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

        for side_key, surface_label in sides:
            target_img_name = f"{piece_id}_{side_key}.jpg"
            img_path = os.path.join(images_dir, target_img_name)
            if not os.path.exists(img_path):
                continue

            target_mask_name = f"{piece_id}_{side_key}_mask.png"
            mask_path = os.path.join(masks_dir, target_mask_name)
            has_mask = 1 if os.path.exists(mask_path) else 0

            # Relative paths for portable Kaggle notebooks
            rel_img = f"images/{target_img_name}"
            rel_mask = f"masks/{target_mask_name}" if has_mask else ""

            w, h, ar = get_image_info(img_path)
            fsize_kb = round(os.path.getsize(img_path) / 1024.0, 1)

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
