# Medanta Hospital - ICU PPE Kit Compliance Monitor

> Real-time AI computer vision system for hospital Intensive Care Units (ICUs) and isolation wards. Tracks medical personnel compliance with Personal Protective Equipment (PPE) including masks, gloves, gowns, and caps to prevent cross-contamination and healthcare-associated infections (HAIs).

---

## 🚀 Key Features
- Real-time YOLOv11 person detection paired with YOLOv11-pose estimation
- Multi-class PPE compliance verification (Mask, Gown, Gloves, Cap)
- Automated violation logging with timestamped incident snapshots
- Configurable ICU bed and sanitization zone boundaries
- Live MJPEG stream with color-coded bounding boxes and compliance HUD
- SQLite event logging and historical audit trail analytics
- Zero-setup bundled web dashboard (runs directly via Python backend)

---

## 🧠 Pre-Installed Models
The repository includes all required pre-trained weights and ONNX model files:
- `backend/models/best.pt`: Fine-tuned PPE classification model
- `backend/models/yolo11n-pose.pt`: YOLOv11 human pose keypoint model
- `backend/models/yolo11n.pt`: YOLOv11 base human detector

---

## 🛠️ Quick Start Guide

### 1. Prerequisites
- **Python**: Version `3.9`, `3.10`, or `3.11` recommended.
- **Hardware**: Compatible with standard CPU. A CUDA-capable NVIDIA GPU is recommended for high-FPS multi-stream processing.
- **Node.js**: *(Optional)* The production UI is **pre-compiled** inside `backend/dist` and served automatically by the Python backend. Node.js is only required if you want to modify the React frontend.

---

### 2. Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Bhavesh-BW/medanta-hospital-icu-ppe-tracker.git
   cd medanta-hospital-icu-ppe-tracker
   ```

2. **Create and activate a virtual environment**:
   - **Windows**:
     ```bash
     python -m venv venv
     venv\Scripts\activate
     ```
   - **Linux / macOS**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

### 3. Running the Service

#### Option A: One-Click Launch Script
- **Windows**: Double-click `start.bat` (or run `start.bat` in CMD)
- **Linux / macOS**: Run `./start.sh`

#### Option B: Direct Python Execution
```bash
cd backend
python ppe_tracker.py
```

---

### 4. Accessing the Dashboard

Once started, open your web browser and navigate to:
```
http://localhost:8186
```

---

## ⚙️ Configuration & Customization

The service can be customized using environment variables or directly in the UI:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TRACKER_BIND` | `0.0.0.0` | Host IP interface to bind the web server |
| `TRACKER_PORT` / `PORT` | `8186` | Port for HTTP API and Web Dashboard |
| `DEVICE` | Auto (`cuda:0` / `cpu`) | Inference device execution provider |

---

## 📁 Repository Structure
```
├── backend/
│   ├── ppe_tracker.py      # Main engine and HTTP server
│   ├── dist/                 # Pre-built production web dashboard
│   ├── models/               # Neural network weights & ONNX models
│   └── requirements.txt      # Python dependencies
├── ui/                       # Frontend source code (React / Vite / Tailwind)
│   ├── src/                  # Components, hooks, and views
│   └── package.json          # Node.js dependencies (for UI development)
├── start.bat                 # Windows one-click launcher
├── start.sh                  # Linux / macOS one-click launcher
├── .gitignore                # Excludes node_modules, temp files & databases
└── README.md                 # Project documentation
```

---

## 🔒 Database & Privacy Notice
Local runtime databases (`*.db`), temporary snapshot photos, and security tokens are excluded from this repository via `.gitignore`. 
When running for the first time, SQLite database tables are automatically initialized with fresh schema structures.
