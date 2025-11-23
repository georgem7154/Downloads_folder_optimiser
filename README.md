# Simple explaination
<img width="708" height="741" alt="image" src="https://github.com/user-attachments/assets/48d586bd-c508-4ad8-bce0-bfbacabad675" />
---
## NOTE ONLY PROCESSES THE FILES 24 HRS BY DEFAULT ADDED OPTION FOR ALL FILES
The integrated code workflow is a **three-step automated organization process** that uses the **Gemini AI Agent** for smart decision-making:

1.  **Core Sort (Deterministic & AI Fallback) 📂:** The script filters **old files** from your Downloads. It moves files based on **extension rules** (e.g., `.zip` $\rightarrow$ Archives). If an extension is **unknown**, the AI Agent is called to classify it and **update the map** for future runs (Self-Learning). Code files are also analyzed by the AI for **project classification**.
2.  **Optional Image Renamer (Batch AI) 🖼️:** If enabled, the script focuses on the new `Images/` folder, sending images in **batches of 10** to the AI Agent to generate descriptive, clean filenames based on the **visual content**.
3.  **Optional PDF Sub-Sorter (AI Content) 📕:** If enabled, the script targets the `Documents/` folder. It uses the **AI Agent** to classify the content of each PDF (using the **filename as a visual content proxy**) and moves it into a specific subfolder like `Documents/Invoices/` or `Documents/Research_Papers/`. 

The whole process runs in a background thread, starts after you enter your saved **Gemini API Key**, and finishes by cleaning up your Downloads into permanent, organized, and searchable folders.

# Installation and running
1. You can run the python file but will have to install all packages

2. run the exe which will auto install all the packages and get you ready to execute on the go, first time run might be slow

# Challenges faced and solved

### 🚀 I. Speed and Efficiency Challenges

| Challenge | Problem Addressed | Solution Implemented |
| :--- | :--- | :--- |
| **C1: Inefficient API Usage** | Processing individual images was slow and created unnecessary network overhead. | **Batch Processing:** Implemented the core logic to group files into **batches of 10** (`BATCH_SIZE`) and send them in a single multimodal request to the Gemini API, drastically increasing throughput. |
| **C2: API Rate Limiting** | Sending too many consecutive requests could lead to temporary API blocks or errors. | **Optional 5-Second Delay:** Included a configurable `use_delay` variable that triggers a `time.sleep(5)` pause between batches, mitigating rate limit risk. |
| **C3: Redundant Processing** | Rerunning the script would waste quota by attempting to rename files already completed. | **Suffix Exclusion Logic:** Added the condition `PROCESSED_SUFFIX not in f` to the file listing, marking completed files (e.g., with the `_DESC` suffix) as done and excluding them from future processing. |

---

### 🧠 II. Intelligence and Adaptability Challenges

| Challenge | Problem Addressed | Solution Implemented |
| :--- | :--- | :--- |
| **C4: Handling New Extensions** | The organizer couldn't classify files with extensions not in the map. | **Structured AI Fallback (Self-Learning):** The **Gemini Agent** suggests a category for unknown extensions (`FolderRecommendation`), which is immediately saved to **`extension_map.json`** for future use. |
| **C5: Semantic Code Sorting** | Basic extensions (`.py`, `.js`) offer no context for project classification. | **Code Content Analysis:** The AI Agent analyzes the **first 50 lines of code** using a dedicated schema (`CodeClassification`) to infer the **project name** (e.g., 'Web\_Scraper') for precise sub-sorting. |
| **C6: PDF Sub-Organization** | Basic sorting leaves the `Documents/` folder cluttered. | **PDF Content Proxy:** The AI Agent uses the **PDF filename** as a robust proxy for content and suggests a semantic sub-category (e.g., 'Invoices' or 'Tax\_Forms') for deeper organization. |
| **C7: Configuration Persistence** | The Gemini API key and settings needed to be remembered between application sessions. | **Hidden Config File:** Implemented `load_config` and `save_config` to store the **API Key** in a persistent, hidden local file (`.file_organizer_config.txt`). |

---

### 🛡️ III. Stability and Integrity Challenges

| Challenge | Problem Addressed | Solution Implemented |
| :--- | :--- | :--- |
| **C8: Naming Conflict (Critical Error)** | The boolean argument (`run_pdf_sorter`) conflicted with the function name, leading to the **`bool object not callable`** error. | **Function Renaming:** Core execution functions were explicitly renamed (e.g., `run_pdf_sorter` $\rightarrow$ **`execute_pdf_sorter`**) to resolve the conflict and ensure stable execution. |
| **C9: API Transient Failures** | Network spikes or brief rate limits could cause files to fail processing permanently. | **Robust Retry Mechanism:** All critical Agent calls are wrapped in **`try/except/retry`** logic (`MAX_RETRIES` = 3, `RETRY_DELAY` = 10s) to overcome transient failures. |
| **C10: Folder Management** | Files moved by the organizer could conflict with existing files in the archive. | **Duplicate Handling:** The `shutil.move` operations include **exception handling** to gracefully log and skip duplicates, maintaining data integrity. |
| **C11: UI Responsiveness** | The time-consuming sorting and API calls would freeze the application window. | **Multi-threading:** All heavy processing (I/O and API calls) is executed on a **separate Python thread**, keeping the UI responsive. |


# Detailed Architecture Explained

## 1. Initialization and Setup Phase ⚙️

This phase occurs immediately when the `start_processing` method is called:

* **Configuration Loading:** The application reads two separate config locations:
    1.  The hidden **`.file_organizer_config.txt`**: This loads the persistent data, primarily the **Gemini API Key** and the default target folder path, ensuring you don't have to re-enter the key.
    2.  The JSON file **`extension_map.json`**: This loads the **Rule-Based Mapping** (e.g., `.pdf` $\rightarrow$ "Documents") and the list of **`Exclusions`**.
* **UI State:** The **START ORGANIZATION** button is disabled, and the log is cleared.
* **Thread Initiation:** The application launches a separate **Python thread** to run the heavy processing (sorting, renaming) so the main UI remains responsive and doesn't freeze. The boolean states of the three checkboxes are passed to this thread.

---

## 2. Core File Sorting and AI Learning (First Pass) 📂

The `organize_downloads` function iterates through every file in the Downloads folder, applying the core organization logic. This is where the initial AI classification happens:

| Step | Logic | Action/Outcome |
| :--- | :--- | :--- |
| **A. Filter** | Check if the file is **excluded** (by name/extension in the map) or if it's **too recent** (modified in the last 24 hours). | Skips the file and continues the loop. |
| **B. Rule-Based Sort** | The file extension is looked up in the `extension_map.json`. | If a match is found (e.g., `.zip`), the file is flagged for movement to the corresponding folder (e.g., "Archives"). |
| **C. Semantic Code Analysis** | If the rule-based sort flagged the file as **"Code"** (e.g., `.py`), the agent is triggered to read the file's content. | The **Gemini Agent** suggests a specific **project folder** (e.g., "Web\_Scraper"). The file is marked for movement to `Organized_Archive/Code_Projects/Web_Scraper/`. |
| **D. Extension Fallback (Self-Learning)** | If **no rule matches** (a completely new extension, like `.blend`), the agent is triggered to classify the extension. | The **Gemini Agent** suggests a folder ("3D\_Assets"). This new mapping (`.blend` $\rightarrow$ "3D\_Assets") is immediately saved back to **`extension_map.json`**, teaching the system for the next run. |
| **E. Final Move** | The file is moved from the **Downloads** folder to the correct final destination inside the **`Organized_Archive`** folder. | The process prepares the sub-folders ("Images", "Documents", "Code\_Projects") for the next two optional stages. |

---

## 3. Optional Specialized Sorting Stages 🖼️📕

If the user checked the corresponding boxes, the following sequential stages run on the sorted folders within `Organized_Archive`.

### Stage 3A: AI Image Renaming (`execute_image_renamer`)
(Runs only if the **"Run Image Renamer"** box is checked.)

* **Target:** Files in the **`Organized_Archive/Images`** folder.
* **Process:** The logic iterates through eligible images (those without the `_DESC` suffix) and processes them in batches of 10. The **Gemini Agent** analyzes the **visual content** of the images and suggests a descriptive filename (`New_York_Skyscraper_DESC.jpg`).
* **Outcome:** Images are renamed based on their content, improving long-term findability.

### Stage 3B: AI PDF Sub-Sorting (`execute_pdf_sorter`)
(Runs only if the **"Run PDF Sub-Sorter"** box is checked.)

* **Target:** Files in the **`Organized_Archive/Documents`** folder, specifically `.pdf` files.
* **Process:** For each PDF, the agent is given the **filename** and the list of current subfolders (e.g., "Invoices", "Research\_Papers"). The **Gemini Agent** classifies the document's purpose.
* **Outcome:** The PDF is moved into a new, semantic sub-folder (e.g., `Documents/Invoices/`), further segmenting the general documents. 

---

## 4. Cleanup and Completion Phase ✅

* **Final Count:** The script reports the total files moved and renamed.
* **UI Re-enablement:** The main thread signals the UI to **re-enable** the **START ORGANIZATION** button, indicating that the processing is complete and the application is ready for the next run.

  
