import shutil
import json
from pathlib import Path
from datetime import datetime, timedelta
import os
import re
import time
import threading
import uuid
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog
import customtkinter as ctk

# --- CONFIGURATION & PATHS ---
GEMINI_MODEL = "gemini-2.5-flash"
MAX_RETRIES = 3
RETRY_DELAY = 10

# Dynamic paths resolved relative to the user's home directory
DOWNLOADS_DIR = Path.home() / "Downloads"
ARCHIVE_DIR = DOWNLOADS_DIR / "Organized_Archive"
# UPDATED: Extension map is now inside the archive folder
EXTENSION_MAP_FILE = ARCHIVE_DIR / "extension_map.json"
CONFIG_FILE = Path.home() / ".file_organizer_config.txt" # Hidden file for API Key persistence

# Image Renamer specific constants
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp')
PROCESSED_SUFFIX = "_DESC" 
BATCH_SIZE = 10 

# --- LOG COLORS ---
COLOR_SUCCESS = "#27AE60"
COLOR_ERROR = "#C0392B"
COLOR_INFO = "#2980B9"
COLOR_RETRY = "#F39C12"
COLOR_IMAGE = "#AF7AC5"
COLOR_PDF = "#E74C3C" 

# --- GLOBAL CLIENT ---
gemini_client = None

# =========================================================================
# I. CORE DATA STRUCTURES & CONFIGURATION MANAGEMENT
# =========================================================================

# Pydantic Schemas for Agents
class FolderRecommendation(BaseModel):
    suggested_folder_name: str = Field(description="The recommended folder name.")
    is_new_category: bool = Field(description="True if this is a new category.")

class CodeClassification(BaseModel):
    project_name: str = Field(description="The primary project or topic this code belongs to.")
    suggested_folder: str = Field(description="The final recommended folder name based on content.")

class ImageDescription(BaseModel):
    original_filename: str = Field(description="The full original filename.")
    short_title: str = Field(description="A concise, descriptive, 3-5 word title.")

class BatchDescription(BaseModel):
    descriptions: list[ImageDescription] = Field(description="A list of descriptions.")

class PdfClassification(BaseModel):
    suggested_subfolder: str = Field(description="The specific subfolder name.")
    is_new_subfolder: bool = Field(description="True if this subfolder is new to the Documents folder.")

# --- CONFIG MANAGEMENT (Corrected) ---

def load_config() -> tuple[str, str]:
    """Reads API key and folder path from the hidden config file."""
    api_key = ""
    folder_path = str(DOWNLOADS_DIR)
    
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                    elif line.startswith("DEFAULT_FOLDER_PATH="):
                        folder_path = line.split("=", 1)[1].strip()
    except Exception as e:
        print(f"Warning: Could not read config file. Error: {e}")
        
    return api_key, folder_path

def save_config(api_key: str, folder_path: str):
    """Writes the current API key and folder path to the hidden config file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            f.write(f"GEMINI_API_KEY={api_key}\n")
            f.write(f"DEFAULT_FOLDER_PATH={folder_path}\n")
    except Exception as e:
        print(f"Warning: Could not save config file. Error: {e}")


def log_message(log_widget: scrolledtext.ScrolledText, message: str, tag: str = None):
    """Inserts a message into the log widget with an optional color tag."""
    log_widget.config(state=tk.NORMAL)
    log_widget.insert(tk.END, message, tag)
    log_widget.see(tk.END)
    log_widget.config(state=tk.DISABLED)

def load_extension_map() -> dict:
    """Loads the extension mapping, including Exclusions, from the JSON file."""
    default_map = {
        "Images": [".jpg", ".jpeg", ".png", ".gif", ".ico", ".webp", ".tiff"],
        "Documents": [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv", ".epub", ".odt"],
        "Installers": [".exe", ".msi", ".dmg", ".pkg"],
        "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
        "Code": [".py", ".js", ".html", ".css", ".md", ".json", ".log"],
        "Audio": [".mp3", ".wav", ".aac", ".flac"],
        "Video": [".mp4", ".mov", ".mkv", ".avi"],
        "Exclusions": [".temp", ".lock", "README.md", "desktop.ini"]
    }
    
    # Ensure the archive directory exists before looking for the map file
    ARCHIVE_DIR.mkdir(exist_ok=True)
    
    try:
        if EXTENSION_MAP_FILE.exists():
            with open(EXTENSION_MAP_FILE, 'r') as f:
                return json.load(f)
        else:
            with open(EXTENSION_MAP_FILE, 'w') as f:
                json.dump(default_map, f, indent=4)
            return default_map
            
    except Exception as e:
        print(f"Warning: Could not load or save map file. Using default. Error: {e}")
        return default_map

def update_extension_map(ext_map: dict, extension: str, folder_name: str, log_widget: scrolledtext.ScrolledText) -> None:
    """Adds a new extension/category mapping and saves the map back to the JSON file."""
    
    safe_folder_name = re.sub(r'[^\w\s-]', '', folder_name).replace(' ', '_')
    extension = extension.lower()

    if safe_folder_name not in ext_map:
        ext_map[safe_folder_name] = []
    
    if extension not in ext_map[safe_folder_name]:
        ext_map[safe_folder_name].append(extension)
        log_message(log_widget, f"  -> MAP UPDATED: Added '{extension}' to category '{safe_folder_name}'.\n", "success")
        
        try:
            with open(EXTENSION_MAP_FILE, 'w') as f:
                json.dump(ext_map, f, indent=4)
        except Exception as e:
            log_message(log_widget, f"  -> FATAL: Could not write map to disk. {e}\n", "error")

# =========================================================================
# II. AGENT FUNCTIONS (Core Logic)
# =========================================================================

def get_folder_recommendation(extension: str, existing_folders: list[str], log_widget: scrolledtext.ScrolledText) -> FolderRecommendation | None:
    """Agent for classifying unknown file extensions."""
    global gemini_client
    if gemini_client is None: return None
    system_instruction = (
        "You are an expert file organizer. The input is an unknown file extension. "
        "Recommend a folder name. The existing categories are: "
        f"[{', '.join(existing_folders)}]. "
        "Use an existing folder if appropriate, or suggest a new, clean category (e.g., 'Blender_Files')."
    )
    prompt = f"Classify the unknown file extension '{extension}' and suggest a folder."
    
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=FolderRecommendation,
                )
            )
            return FolderRecommendation(**json.loads(response.text))
        except APIError as e:
            retries += 1
            log_message(log_widget, f"  -> EXTENSION AGENT ERROR (Attempt {retries}): {e}\n", "error")
            time.sleep(RETRY_DELAY)
        except Exception as e:
            log_message(log_widget, f"  -> UNEXPECTED EXTENSION AGENT ERROR: {e}\n", "error")
            return None
    return None

def analyze_code_content(file_path: Path, log_widget: scrolledtext.ScrolledText) -> CodeClassification | None:
    """Agent for semantic classification of code files based on content."""
    global gemini_client
    if gemini_client is None: return None

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content_snippet = "".join(f.readlines()[:50])
            
        system_instruction = (
            "You are an expert software engineer and project classifier. "
            "Analyze the code snippet. Infer its main purpose, language, and project "
            "it belongs to. Provide a clean, project-based folder classification. "
            "Use snake_case for names (e.g., 'Web_Scraper', 'Financial_Model')."
        )
        prompt = f"Code Snippet from {file_path.name}:\n\n{content_snippet}"
        
        retries = 0
        while retries < MAX_RETRIES:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=CodeClassification,
                )
            )
            return CodeClassification(**json.loads(response.text))
        
    except Exception as e:
        log_message(log_widget, f"  -> Code Analysis Failed for {file_path.name}: {e}\n", "error")
        return None
    return None


def get_batch_info_from_images_renamer(image_batch: list[tuple[str, Image.Image]], log_widget: scrolledtext.ScrolledText) -> dict[str, ImageDescription] | None:
    """Sends a batch of images to Gemini for renaming."""
    global gemini_client
    if gemini_client is None: return None

    contents = ["Analyze the following batch of images. For EACH image, generate a concise, descriptive, 3-5 word title suitable for renaming. Return the complete structured JSON array."]
    for filename, img in image_batch:
        contents.append(img)
        contents.append(f"Image File: {filename}")
    
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction="You are an expert file naming assistant. Your only output must be the requested JSON structure.",
                    response_mime_type="application/json",
                    response_schema=BatchDescription,
                )
            )
            json_data = json.loads(response.text)
            batch_result = BatchDescription(**json_data)
            return {desc.original_filename: desc for desc in batch_result.descriptions}
        except Exception as e:
            retries += 1
            log_message(log_widget, f"  -> IMAGE AGENT ERROR (Attempt {retries}): {e}\n", "error")
            if retries < MAX_RETRIES: time.sleep(RETRY_DELAY)
            else: return None
    return None

def retry_failed_file_renamer(folder_path: Path, original_filename: str, log_widget: scrolledtext.ScrolledText) -> bool:
    """Handles individual image retry on API failure."""
    global gemini_client
    if gemini_client is None: return False

    log_message(log_widget, f"\n[RETRY] Attempting single-file retry for: {original_filename}\n", "retry")
    
    ext = os.path.splitext(original_filename)[1]
    temp_unique_id = uuid.uuid4().hex[:8]
    temp_filename = f"temp_retry_{temp_unique_id}{ext}"
    original_file_path = folder_path / original_filename
    temp_file_path = folder_path / temp_filename

    try:
        os.rename(original_file_path, temp_file_path)
        img = Image.open(temp_file_path)
        
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[img, f"Analyze this image and give a concise, descriptive, 3-5 word title. Return only the title in a simple JSON format like: {{\"short_title\": \"your title\"}}"],
            config=types.GenerateContentConfig(
                system_instruction="You are an expert file naming assistant. Your only output must be a single JSON object with the key 'short_title'.",
                response_mime_type="application/json",
            )
        )
        
        json_data = json.loads(response.text)
        short_title = json_data.get('short_title')
        if not short_title: raise ValueError("API returned no short_title.")

        cleaned_title = re.sub(r'[^\w\s-]', '', short_title).strip().replace(' ', '_').replace('-', '_')
        new_base_name = f"{cleaned_title}{PROCESSED_SUFFIX}"
        new_filename = f"{new_base_name}{ext}"
        new_file_path = folder_path / new_filename

        counter = 1
        while new_file_path.exists():
            new_filename = f"{new_base_name}_{counter}{ext}"
            new_file_path = folder_path / new_filename
            counter += 1

        os.rename(temp_file_path, new_file_path) 
        log_message(log_widget, f"  -> RETRY SUCCESS: {new_filename}\n", "success")
        return True

    except Exception as e:
        log_message(log_widget, f"  -> RETRY FAILED for {original_filename}: {e}\n", "error")
        try:
            os.rename(temp_file_path, original_file_path)
        except:
            log_message(log_widget, "  -> FATAL: Could not restore file name. Check permissions.\n", "error")
        return False


def classify_pdf_by_image(file_path: Path, existing_subfolders: list[str], log_widget: scrolledtext.ScrolledText) -> PdfClassification | None:
    """Agent for PDF subfolder classification (using filename as fallback)."""
    global gemini_client
    if gemini_client is None: return None

    # NOTE: In a production app, you would use a library like 'pdf2image' or 'PyMuPDF' here
    # to extract the first page image before sending it to the agent.
    
    prompt = f"The PDF file is named '{file_path.name}'. Infer its content. Existing subfolders are: [{', '.join(existing_subfolders)}]. Classify it."
    contents = [prompt]
    
    system_instruction = (
        "You are an expert document sorter. You must classify the document into one of the existing subfolders or suggest a new one. "
        "Suggested names must be clean and reflect content (e.g., 'Invoices', 'Research_Papers')."
    )

    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=PdfClassification,
                )
            )
            return PdfClassification(**json.loads(response.text))

        except APIError as e:
            retries += 1
            log_message(log_widget, f"  -> PDF AGENT ERROR (Attempt {retries}): {e}\n", "error")
            time.sleep(RETRY_DELAY)
        except Exception:
            return None
    return None

# =========================================================================
# III. ORCHESTRATION FUNCTIONS (Modified with Folder Moving Logic)
# =========================================================================

def organize_downloads(log_widget: scrolledtext.ScrolledText, process_all_files: bool):
    """The main file sorting logic, now including moving existing sub-folders."""
    log_message(log_widget, f"\n--- Starting core file sorting: {DOWNLOADS_DIR} ---\n", "info")
    
    if not DOWNLOADS_DIR.exists():
        log_message(log_widget, f"❌ Error: Downloads directory not found.", "error")
        return 0

    EXT_MAP = load_extension_map()
    ARCHIVE_DIR.mkdir(exist_ok=True)
    
    # Define the new destination for all folders
    FOLDERS_DEST_DIR = ARCHIVE_DIR / "Folders"
    FOLDERS_DEST_DIR.mkdir(exist_ok=True)
    
    CUTOFF_TIME = datetime.now() - timedelta(hours=24)
    
    exclusion_list = [item.lower() for item in EXT_MAP.get("Exclusions", [])]
    existing_categories = [k for k in EXT_MAP.keys() if k != "Exclusions"]
    
    files_processed = 0
    folders_processed = 0 # New counter for folders
    
    for item in DOWNLOADS_DIR.iterdir():
        
        # --- FOLDER HANDLING LOGIC ---
        if item.is_dir():
            # Skip the main archive, the 'Folders' destination, and other necessary system/config directories
            if item.name == ARCHIVE_DIR.name or item.name == FOLDERS_DEST_DIR.name:
                continue
            
            try:
                # Move the folder into the 'Folders' destination directory
                shutil.move(str(item), str(FOLDERS_DEST_DIR / item.name))
                log_message(log_widget, f"📂 Moved Folder: {item.name} -> Folders/\n", "success")
                folders_processed += 1
            except Exception as e:
                log_message(log_widget, f"❌ Folder move error with {item.name}: {e}\n", "error")
            continue
        # --- END FOLDER HANDLING LOGIC ---

        # Skip specific config files now that we've handled directories
        # Only check CONFIG_FILE as EXTENSION_MAP_FILE is now inside ARCHIVE_DIR
        if item.name == CONFIG_FILE.name:
            continue
            
        # 1. Exclusion Check ⛔
        if item.name.lower() in exclusion_list or item.suffix.lower() in exclusion_list:
            log_message(log_widget, f"⛔ Excluded: {item.name}\n", "error")
            continue
            
        # 2. TIME-GATING CHECK (Conditional based on new checkbox)
        if not process_all_files:
            file_mod_time = datetime.fromtimestamp(item.stat().st_mtime)
            if file_mod_time >= CUTOFF_TIME:
                log_message(log_widget, f"⏰ Recent: {item.name} (Skipping)\n", "info")
                continue
            
        log_message(log_widget, f"Processing file: {item.name}\n", "info")
        
        ext = item.suffix.lower()
        destination_folder = None
        
        # 3. Rule-Based Classification
        for category, extensions in EXT_MAP.items():
            if ext in extensions:
                destination_folder = category
                break

        # 4. Semantic Code Analysis (if classified as 'Code')
        if destination_folder == "Code":
            code_info = analyze_code_content(item, log_widget)
            if code_info and code_info.suggested_folder:
                semantic_folder = re.sub(r'[^\w\s-]', '', code_info.suggested_folder).replace(' ', '_')
                final_folder_path = ARCHIVE_DIR / "Code_Projects" / semantic_folder
                destination_folder = Path("Code_Projects") / semantic_folder
                final_folder_path.mkdir(parents=True, exist_ok=True)
                log_message(log_widget, f"  -> CLASSIFIED AS: {semantic_folder}\n", "success")

        # 5. Agent-Based Extension Fallback (if still unclassified)
        elif destination_folder is None:
            recommendation = get_folder_recommendation(ext, existing_categories, log_widget)
            
            if recommendation and recommendation.suggested_folder_name:
                destination_folder = recommendation.suggested_folder_name
                update_extension_map(EXT_MAP, ext, destination_folder, log_widget)
            else:
                destination_folder = "Unsorted_Agent_Failed"

        # 6. Final Move (for files)
        if destination_folder:
            if isinstance(destination_folder, Path):
                 target_dir = ARCHIVE_DIR.joinpath(destination_folder)
                 folder_name_for_log = destination_folder.as_posix()
            else:
                target_dir = ARCHIVE_DIR / destination_folder
                folder_name_for_log = destination_folder
                target_dir.mkdir(parents=True, exist_ok=True) 

            try:
                shutil.move(str(item), str(target_dir / item.name))
                log_message(log_widget, f"✅ Moved: {item.name} -> {folder_name_for_log}\n", "success")
                files_processed += 1
            except Exception as e:
                log_message(log_widget, f"❌ Move error with {item.name}: {e}\n", "error")

    log_message(log_widget, f"\n--- Core file sorting complete. {files_processed} files and {folders_processed} folders processed. ---\n", "info")
    return files_processed

def execute_image_renamer(images_folder: Path, log_widget: scrolledtext.ScrolledText, use_delay: bool) -> int:
    """[RENAMED FUNCTION] Runs the full image batch renaming process on the *sorted* Images folder."""
    log_widget.tag_config('image', foreground=COLOR_IMAGE, font=('Courier', 10, 'bold'))
    log_message(log_widget, f"\n--- STARTING OPTIONAL IMAGE RENAMER MODULE ---", "image")

    if not images_folder.is_dir():
           log_message(log_widget, f"Target folder not found: {images_folder.name}. Skipping Image Renamer.\n", "error")
           return 0

    all_files = [f.name for f in images_folder.iterdir() if f.is_file() and f.name.lower().endswith(IMAGE_EXTENSIONS) and PROCESSED_SUFFIX not in f.name]
    total_eligible_files = len(all_files)
    batch_processed_count = 0
    file_index = 0
    failed_files_for_retry = []
    
    log_message(log_widget, f"Found {total_eligible_files} images in {images_folder.name}/ to rename.\n", "image")

    while file_index < total_eligible_files:
        current_batch_files = all_files[file_index:file_index + BATCH_SIZE]
        image_batch = []
        
        for filename in current_batch_files:
            file_path = images_folder / filename
            try:
                img = Image.open(file_path) 
                image_batch.append((filename, img))
            except Exception as e:
                failed_files_for_retry.append(filename)

        if image_batch:
            result_map = get_batch_info_from_images_renamer(image_batch, log_widget)
            
            if result_map:
                for original_filename, _ in image_batch:
                    if original_filename not in result_map:
                        failed_files_for_retry.append(original_filename)
                        continue
                    
                    description_info = result_map[original_filename]
                    try:
                        ext = os.path.splitext(original_filename)[1]
                        cleaned_title = re.sub(r'[^\w\s-]', '', description_info.short_title).strip().replace(' ', '_').replace('-', '_')
                        new_base_name = f"{cleaned_title}{PROCESSED_SUFFIX}"
                        new_filename = f"{new_base_name}{ext}"
                        
                        current_path = images_folder / original_filename
                        new_path = images_folder / new_filename
                        
                        os.rename(current_path, new_path) 
                        batch_processed_count += 1
                    except Exception as e:
                        failed_files_for_retry.append(original_filename)
            else:
                failed_files_for_retry.extend(current_batch_files)

        file_index += BATCH_SIZE
        if use_delay and file_index < total_eligible_files:
            time.sleep(5) 
            
    total_renamed = batch_processed_count 
    log_message(log_widget, f"\n--- IMAGE RENAMER COMPLETE. {total_renamed} files renamed. ---\n", "image")
    return total_renamed

def execute_pdf_sorter(log_widget: scrolledtext.ScrolledText):
    """[RENAMED FUNCTION] Runs the PDF sorter on the Documents folder after initial sorting."""
    
    log_widget.tag_config('pdf', foreground=COLOR_PDF, font=('Courier', 10, 'bold'))
    log_message(log_widget, f"\n--- STARTING OPTIONAL PDF SORTER MODULE ---", "pdf")
    
    DOCUMENTS_FOLDER = ARCHIVE_DIR / "Documents"
    if not DOCUMENTS_FOLDER.is_dir():
           log_message(log_widget, f"Target folder not found: {DOCUMENTS_FOLDER.name}. Skipping PDF sorter.\n", "error")
           return 0

    files_processed = 0
    
    existing_subfolders = [d.name for d in DOCUMENTS_FOLDER.iterdir() if d.is_dir()]

    for item in DOCUMENTS_FOLDER.iterdir():
        if item.is_file() and item.suffix.lower() == '.pdf':
            log_message(log_widget, f"Classifying PDF: {item.name}\n", "pdf")
            
            recommendation = classify_pdf_by_image(item, existing_subfolders, log_widget)
            
            if recommendation and recommendation.suggested_subfolder:
                subfolder_name = re.sub(r'[^\w\s-]', '', recommendation.suggested_subfolder).replace(' ', '_')
                target_dir = DOCUMENTS_FOLDER / subfolder_name
                target_dir.mkdir(parents=True, exist_ok=True)
                
                try:
                    shutil.move(str(item), str(target_dir / item.name))
                    log_message(log_widget, f"✅ PDF Moved to: {subfolder_name}\n", "success")
                    files_processed += 1
                    
                    if recommendation.is_new_subfolder and subfolder_name not in existing_subfolders:
                        existing_subfolders.append(subfolder_name)
                        
                except Exception as e:
                    log_message(log_widget, f"❌ Failed to move {item.name}: {e}\n", "error")
            else:
                 log_message(log_widget, f"⚠️ Could not classify {item.name}. Leaving in Documents root.\n", "pdf")

    log_message(log_widget, f"\n--- PDF Sorter Complete. {files_processed} PDFs sorted. ---\n", "pdf")
    return files_processed


# =========================================================================
# IV. CUSTOMTKINTER UI IMPLEMENTATION
# =========================================================================

class FileOrganizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue") 
        
        self.title("AI File Organizer & Renamer")
        self.geometry("700x700")
        
        default_api_key, default_folder_path = load_config()
        
        self.api_key_var = tk.StringVar(self, value=default_api_key) 
        self.folder_path_var = tk.StringVar(self, value=default_folder_path) 
        self.run_image_renamer_var = tk.BooleanVar(self, value=False) 
        self.run_pdf_sorter_var = tk.BooleanVar(self, value=False) 
        self.use_delay_var = tk.BooleanVar(self, value=True) 
        # NEW: Variable for time-gating bypass
        self.process_all_files_var = tk.BooleanVar(self, value=False)
        
        global gemini_client
        gemini_client = None

        self.setup_ui()

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1) 

        ctk.CTkLabel(self, text="🧠 AI File Organizer 📂", 
                     font=ctk.CTkFont(size=30, weight="bold")).grid(row=0, column=0, pady=(20, 10), padx=20, sticky="ew")

        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        control_frame.grid_columnconfigure(1, weight=1)
        control_frame.grid_columnconfigure(2, weight=1) 

        # ROW 0: Gemini API Key Field
        ctk.CTkLabel(control_frame, text="Gemini API Key:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        api_entry = ctk.CTkEntry(control_frame, textvariable=self.api_key_var, show='*', width=350)
        api_entry.grid(row=0, column=1, padx=10, pady=5, columnspan=2, sticky="ew")
        self.api_key_var.trace_add("write", self.update_client)

        # ROW 1: Target Folder (Readonly)
        ctk.CTkLabel(control_frame, text="Target Folder:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkEntry(control_frame, textvariable=self.folder_path_var, state='readonly', width=350).grid(row=1, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        # ROW 2: Configuration Buttons
        ctk.CTkButton(control_frame, 
                      text="Edit Extension Map 🧠", 
                      command=self.edit_extension_map,
                      fg_color="#3498DB", 
                      hover_color="#2980B9"
                      ).grid(row=2, column=1, padx=(10, 5), pady=10, sticky="ew")

        ctk.CTkButton(control_frame, 
                      text="Edit Exclusion List 🚫", 
                      command=self.edit_exclusion_list,
                      fg_color="#F39C12", 
                      hover_color="#D35400"
                      ).grid(row=2, column=2, padx=(5, 10), pady=10, sticky="ew")
        
        # ROW 3: Optional Renamer Checkboxes 
        checkbox_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        checkbox_frame.grid(row=3, column=0, columnspan=3, pady=5, sticky="w", padx=10)
        
        ctk.CTkCheckBox(checkbox_frame,
                        text="🖼️ Run Image Renamer",
                        variable=self.run_image_renamer_var,
                        onvalue=True, offvalue=False,
                        text_color=COLOR_IMAGE
                        ).pack(side=tk.LEFT, padx=10)
        
        ctk.CTkCheckBox(checkbox_frame,
                        text="📕 Run PDF Sub-Sorter",
                        variable=self.run_pdf_sorter_var,
                        onvalue=True, offvalue=False,
                        text_color=COLOR_PDF
                        ).pack(side=tk.LEFT, padx=10)

        # NEW CHECKBOX: Process All Files
        self.process_all_checkbox = ctk.CTkCheckBox(checkbox_frame,
                        text="⏳ Process All Files (Ignore 24hr limit)",
                        variable=self.process_all_files_var,
                        onvalue=True, offvalue=False,
                        text_color=COLOR_INFO,
                        state=tk.DISABLED # Starts DISABLED as requested
                        )
        self.process_all_checkbox.pack(side=tk.LEFT, padx=10)


        # ROW 4: Start Button
        self.start_button = ctk.CTkButton(control_frame, text="START ORGANIZATION", command=self.start_processing, 
                                             fg_color="#15F000", hover_color="#15CC00", 
                                             font=ctk.CTkFont(size=24, weight="bold"))
        self.start_button.grid(row=4, column=0, columnspan=3, pady=(10, 20), padx=10, sticky="ew") 

        # Log Output
        ctk.CTkLabel(self, text="Processing Log:").grid(row=2, column=0, padx=20, pady=(10, 0), sticky="sw")
        self.log_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, height=15, state='disabled', 
                                                     bg=ctk.ThemeManager.theme['CTkEntry']['fg_color'][0], 
                                                     fg=ctk.ThemeManager.theme['CTkEntry']['text_color'][0],
                                                     bd=0, relief=tk.FLAT)
        self.log_widget.grid(row=4, column=0, padx=20, pady=(5, 20), sticky="nsew")
        
        self.log_widget.tag_config('success', foreground=COLOR_SUCCESS, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('error', foreground=COLOR_ERROR, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('info', foreground=COLOR_INFO)
        self.log_widget.tag_config('retry', foreground=COLOR_RETRY, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('image', foreground=COLOR_IMAGE, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('pdf', foreground=COLOR_PDF, font=('Courier', 10, 'bold'))

        self.update_client()
    
    def edit_exclusion_list(self):
        """Opens a dialog to view/edit the Exclusions list using a single comma-separated line."""
        current_map = load_extension_map()
        current_exclusions = current_map.get("Exclusions", [])
        current_string = ", ".join(current_exclusions)
        
        new_exclusions_string = simpledialog.askstring(
            "Edit Exclusion List (Comma-Separated)",
            "Enter extensions or filenames to exclude (e.g., .temp, desktop.ini, .lock).",
            initialvalue=current_string,
            parent=self
        )
        
        if new_exclusions_string is not None:
            updated_list = [item.strip().lower() for item in new_exclusions_string.split(',') if item.strip()]
            current_map["Exclusions"] = updated_list
            try:
                with open(EXTENSION_MAP_FILE, 'w') as f:
                    json.dump(current_map, f, indent=4)
                messagebox.showinfo("Success", "Exclusion list saved. Restart processing to apply.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save exclusion list: {e}")
    
    def edit_extension_map(self):
        """Opens a dialog to inform the user how to edit the main map file."""
        messagebox.showinfo(
            "Edit Extension Map",
            # UPDATED DIALOG TEXT
            f"To edit the main folder categories and extensions, please manually open the configuration file, now located inside the Organized_Archive folder:\n\n{EXTENSION_MAP_FILE}\n\n"
            "Ensure the file remains valid JSON format after editing."
        )

    def update_client(self, *args):
        """Initializes the Gemini client or updates the status based on the key."""
        global gemini_client
        api_key = self.api_key_var.get()
        
        gemini_client = None
        
        if len(api_key) > 10:
            try:
                gemini_client = genai.Client(api_key=api_key)
                self.start_button.configure(state=tk.NORMAL, text="START ORGANIZATION (Ready)", fg_color="#15F000", hover_color="#15CC00")
                self.process_all_checkbox.configure(state=tk.NORMAL) # ENABLE the new checkbox
            except Exception:
                self.start_button.configure(state=tk.DISABLED, text="START ORGANIZATION (API Key Error)", fg_color=COLOR_ERROR, hover_color="#A93226")
                self.process_all_checkbox.configure(state=tk.DISABLED) # DISABLE on error
        else:
            self.start_button.configure(state=tk.DISABLED, text="START ORGANIZATION (Enter API Key)", fg_color="gray", hover_color="dim gray")
            self.process_all_checkbox.configure(state=tk.DISABLED) # DISABLE on empty key


    def start_processing(self):
        """Starts the main organization thread AND saves the current API key."""
        
        if gemini_client is None:
            messagebox.showerror("Error", "Gemini API Client is not initialized. Check your key.")
            return

        # Save Config
        folder = self.folder_path_var.get()
        api_key = self.api_key_var.get()
        save_config(api_key, folder)
        
        # UI State Reset
        self.log_widget.config(state=tk.NORMAL); self.log_widget.delete(1.0, tk.END); self.log_widget.config(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED, text="PROCESSING... DO NOT CLOSE", fg_color='orange')
        
        # Get boolean values from the checkbox variables
        run_renamer = self.run_image_renamer_var.get()
        run_pdf_sorter = self.run_pdf_sorter_var.get()
        use_delay = self.use_delay_var.get()
        # NEW: Get process all files flag
        process_all_files = self.process_all_files_var.get()

        # Run processing thread with the flags
        processing_thread = threading.Thread(target=self._run_processing_thread, args=(self.log_widget, run_renamer, run_pdf_sorter, use_delay, process_all_files))
        processing_thread.start()

    def _run_processing_thread(self, log_widget, run_renamer: bool, run_pdf_sorter: bool, use_delay: bool, process_all_files: bool):
        """Internal function to run the sequential processing: Core Sort -> Renamer -> PDF Sort."""
        total_files_moved = 0
        total_images_renamed = 0
        total_pdfs_sorted = 0
        
        try:
            # STEP 1: Core File Organization (Passing the new flag)
            total_files_moved = organize_downloads(log_widget, process_all_files) 
            
            # STEP 2: Optional Image Renaming (Runs on Images folder)
            if run_renamer:
                images_folder = ARCHIVE_DIR / "Images"
                total_images_renamed = execute_image_renamer(images_folder, log_widget, use_delay) 
                
            # STEP 3: Optional PDF Sub-Sorting (Runs on Documents folder)
            if run_pdf_sorter: 
                total_pdfs_sorted = execute_pdf_sorter(log_widget)

            final_message = f"Process Complete!\nFiles Organized: {total_files_moved}\nImages Renamed: {total_images_renamed}\nPDFs Sorted: {total_pdfs_sorted}"
            messagebox.showinfo("Process Complete", final_message)
            
        except Exception as e:
             final_message = f"Critical Error: {e}"
             messagebox.showerror("Critical Error", final_message)
        finally:
            self.after(0, lambda: self.start_button.configure(state=tk.NORMAL, text="START ORGANIZATION (Done)", fg_color="#15F000", hover_color="#15CC00"))


if __name__ == "__main__":
    app = FileOrganizerApp()
    app.mainloop()