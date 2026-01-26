# Meeting PDF Summarizer

An AI-powered tool that generates professional meeting summaries from transcripts using Ollama. The tool processes meeting transcripts and creates structured PDF summaries with key decisions, action items, risks, and more.

## Features

- 📝 Processes meeting transcripts in a simple format (Speaker: Text)
- 🤖 Uses Ollama AI to generate structured summaries
- 📄 Creates professional PDF reports with:
  - Meeting title, date, and purpose
  - Executive snapshot
  - Key decisions with owners and effective dates
  - Action items with owners, tasks, and due dates
  - Open questions and unresolved issues
  - Risks, concerns, and constraints
  - Important context and rationale
  - Key metrics, dates, and milestones
  - Follow-up cadence

## Prerequisites

- Python 3.8 or higher
- [Ollama](https://ollama.ai/) installed and running locally
- An Ollama model (default: `qwen2.5:3b`)

## Installation

1. **Install Git (if not already installed):**
   - Windows: Download from https://git-scm.com/download/win
   - Mac: Usually pre-installed, or install via Homebrew: `brew install git`
   - Linux: `sudo apt install git` (Ubuntu/Debian) or `sudo yum install git` (CentOS/RHEL)

2. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/REPO_NAME.git
   cd REPO_NAME
   ```
   
   Replace `YOUR_USERNAME` and `REPO_NAME` with the actual GitHub username and repository name.
   
   Example:
   ```bash
   git clone https://github.com/james/meeting-pdf-summarizer.git
   cd meeting-pdf-summarizer
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Ollama:**
   - Install Ollama from [https://ollama.ai/](https://ollama.ai/)
   - Pull the model you want to use:
     ```bash
     ollama pull qwen2.5:3b
     ```
   - Make sure Ollama is running (it should start automatically)

4. **Configure environment (optional):**
   - Create a `.env` file in the project root if you need custom settings:
     ```
     OLLAMA_URL=http://localhost:11434
     OLLAMA_MODEL=qwen2.5:3b
     ```

## Usage

### Basic Usage

```bash
python main.py --input transcripts/amazon_sample.txt --output out/meeting_summary.pdf
```

### Arguments

- `--input` (required): Path to the transcript text file
- `--output` (required): Path where the PDF will be saved
- `--roles` (optional): Path to roles.json file (default: `roles.json`)

### Transcript Format

Your transcript file should follow this format:

```
Speaker Name: This is what they said.
Another Speaker: This is their response.
Speaker Name: They can continue speaking on multiple lines.
```

Example:
```
Carter: Thanks everyone for joining. The goal of today's meeting is to align on readiness.
James: From a product perspective, the core feature set is defined.
Theo: On the engineering side, the main functionality is implemented.
```

## Sample Transcripts

The repository includes several sample transcripts:
- `transcripts/tiny.txt` - A short example
- `transcripts/amazon_sample.txt` - A comprehensive Amazon-themed example
- `transcripts/test_all_features.txt` - Tests all features

## Project Structure

```
meeting_pdf_summarizer/
├── main.py                 # Main application code
├── requirements.txt        # Python dependencies
├── roles.json             # Role definitions (optional)
├── .gitignore             # Git ignore rules
├── README.md              # This file
├── transcripts/           # Sample transcripts
│   ├── amazon_sample.txt
│   ├── test_all_features.txt
│   └── tiny.txt
└── out/                   # Generated PDFs (gitignored)
```

## Configuration

### Roles File

The `roles.json` file maps speaker names to roles. This is optional but helps the AI understand context:

```json
{
  "roles": [
    { "role": "Executive / Leadership", "description": "..." },
    { "role": "Product Manager", "description": "..." }
  ],
  "name_to_role": {
    "James": "Product Manager",
    "Theo": "Engineering"
  }
}
```

### Environment Variables

Create a `.env` file to customize:

- `OLLAMA_URL`: Ollama API URL (default: `http://localhost:11434`)
- `OLLAMA_MODEL`: Model to use (default: `qwen2.5:3b`)

## Troubleshooting

### Ollama Connection Issues

- Make sure Ollama is running: `ollama list`
- Check if the model is installed: `ollama pull qwen2.5:3b`
- Verify the URL in your `.env` file matches your Ollama setup

### JSON Parsing Errors

If you see JSON parsing errors, the model response might be incomplete. Try:
- Using a larger model
- Increasing `num_predict` in the code (line 65)
- Using a shorter transcript

### Timeout Errors

For very long transcripts, you may need to:
- Increase the timeout in `call_ollama()` function
- Use a faster model
- Split the transcript into smaller chunks

## Contributing

Feel free to submit issues or pull requests!

## License

[Add your license here]

## Author

[Your name]
