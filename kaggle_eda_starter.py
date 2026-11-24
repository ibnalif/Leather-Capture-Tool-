"""
================================================================================
✦ Leather Quality & Defect Dataset — Kaggle EDA Starter Script
================================================================================
This script demonstrates how to load, analyze, and visualize the leather grading
dataset using pandas, matplotlib, and seaborn for Kaggle notebooks.
"""

import os
import pandas as pd
import numpy as np

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

# 1. Load Dataset Metadata
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(SCRIPT_DIR, "dataset")
METADATA_PATH = os.path.join(DATASET_DIR, "metadata.csv")

if not os.path.exists(METADATA_PATH):
    print(f"[ERROR] metadata.csv not found at {METADATA_PATH}")
    print("Run the desktop capture tool or dataset_manager.py to generate it.")
    exit(1)

df = pd.read_csv(METADATA_PATH)
print("=" * 60)
print(f"Loaded {len(df)} samples across {df['piece_id'].nunique()} unique leather hides.")
print("=" * 60)
print(df.info())
print("\nFirst 3 Samples:")
print(df[["sample_id", "side", "surface_type", "grade", "size_category", "defect_count"]].head(3))

# 2. Summary Statistics
print("\n--- Quality Grade Distribution ---")
print(df["grade"].value_counts(dropna=False))

print("\n--- Surface Side Breakdown ---")
print(df["surface_type"].value_counts())

# 3. Defect Analysis
defect_cols = [c for c in df.columns if c.startswith("defect_") and c != "defect_count"]
print("\n--- Defect Occurrences ---")
defect_sums = df[defect_cols].sum().sort_values(ascending=False)
print(defect_sums)

# 4. Visualizations
if MATPLOTLIB_AVAILABLE:
    plt.style.use("seaborn-v0_8-darkgrid" if "seaborn-v0_8-darkgrid" in plt.style.available else "default")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # Plot A: Grade Distribution
    df["grade"].value_counts().plot(kind="bar", ax=axes[0], color="#00E5FF", edgecolor="#222")
    axes[0].set_title("Leather Quality Grade Distribution", fontsize=12, fontweight="bold")
    axes[0].set_xlabel("Grade")
    axes[0].set_ylabel("Count")

    # Plot B: Defect Frequencies
    defect_sums.plot(kind="barh", ax=axes[1], color="#EF4444", edgecolor="#222")
    axes[1].set_title("Defect Type Frequencies", fontsize=12, fontweight="bold")
    axes[1].set_xlabel("Occurrences")

    # Plot C: Piece Size Breakdown
    df["size_category"].value_counts().plot(kind="pie", ax=axes[2], autopct="%1.1f%%", startangle=90,
                                             colors=["#3B82F6", "#10B981", "#F59E0B", "#A78BFA"])
    axes[2].set_title("Hide Size Categories", fontsize=12, fontweight="bold")
    axes[2].set_ylabel("")

    plt.tight_layout()
    eda_plot_path = os.path.join(DATASET_DIR, "eda_overview.png")
    plt.savefig(eda_plot_path, dpi=150)
    print(f"\n[INFO] EDA plot saved to {eda_plot_path}")
    try:
        plt.show()
    except Exception:
        pass
else:
    print("\n[NOTE] matplotlib is not installed. To generate visualization plots, run: pip install matplotlib")

# 5. PyTorch Dataset Example for ML Practitioners
print("""
================================================================================
✦ PyTorch Dataset Boilerplate (for Training Segmentation / Classification)
================================================================================
import cv2
import torch
from torch.utils.data import Dataset

class LeatherDataset(Dataset):
    def __init__(self, csv_file, transform=None):
        self.df = pd.read_csv(csv_file)
        self.transform = transform

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        img_path = os.path.join(DATASET_DIR, row['image_path'])
        image = cv2.imread(img_path)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # Load defect mask if available
        mask = None
        if pd.notna(row['mask_path']) and row['mask_path'] != "":
            mask_path = os.path.join(DATASET_DIR, row['mask_path'])
            if os.path.exists(mask_path):
                mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

        if mask is None:
            mask = np.zeros(image.shape[:2], dtype=np.uint8)

        if self.transform:
            augmented = self.transform(image=image, mask=mask)
            image = augmented['image']
            mask = augmented['mask']

        return image, mask, row['grade']
""")
