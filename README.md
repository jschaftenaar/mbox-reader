# 📧 Mbox Email Reader

A self-hosted, searchable email archive viewer for Gmail Takeout mbox files. Browse your exported emails with a modern web interface without paying for cloud storage.

## 💡 Why This Exists

**Problem**: Google Workspace storage costs add up quickly, especially with years of email history.

**Solution**: Export your Gmail once using [Google Takeout](https://takeout.google.com/), delete it from Gmail, and browse it locally with this tool.

### Benefits

- 💰 **Save Money** - No ongoing storage costs
- 🔒 **Privacy** - Your emails stay on your machine
- 🚀 **Fast Search** - SQLite FTS5 full-text search
- 📱 **Modern UI** - Gmail-style three-column interface
- 🖼️ **Attachments** - View images, download files
- 🏷️ **Labels** - Preserves Gmail labels/folders
- 📦 **Self-Contained** - Runs in Docker, no cloud needed

## ✨ Features

- **Three-column layout** - Labels sidebar, email list, content viewer
- **Full-text search** - Search across from, to, subject, and body
- **Gmail label support** - Filter by Inbox, Sent, Drafts, etc.
- **Attachment handling** - Extract, view images, download files
- **HTML email rendering** - Proper display of formatted emails
- **Pagination** - Fast browsing of large archives (50 emails/page)
- **Progress tracking** - Real-time indexing progress
- **Persistent storage** - SQLite database for quick access

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- Gmail Takeout mbox file

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/mbox-reader.git
   cd mbox-reader
   ```

2. **Get your Gmail data**:
   - Go to [Google Takeout](https://takeout.google.com/)
   - Select "Mail" only
   - Choose "Export once"
   - Download format: `.tgz` or `.zip`
   - Extract the mbox file

3. **Place your mbox file**:
   ```bash
   mkdir -p data
   cp ~/Downloads/Takeout/Mail/*.mbox data/emails.mbox
   ```

4. **Configure (optional)**:
   ```bash
   cp .env.example .env
   # Edit .env to change port or file path
   ```

5. **Start the application**:
   ```bash
   docker-compose up -d
   ```

6. **Access the web interface**:
   ```
   http://localhost:8080
   ```

7. **Index your emails**:
   - Click "Rebuild Index" button in the UI
   - Wait for indexing to complete (30-60 min for 12GB)
   - Progress shown in real-time

## 📖 Usage

### First Time Setup

1. **Start the app** - `docker-compose up -d`
2. **Open browser** - http://localhost:8080
3. **Click "Rebuild Index"** - Extracts emails and attachments
4. **Wait for completion** - Progress bar shows status
5. **Browse your emails** - All features now available

### Daily Use

- **Browse by label** - Click labels in left sidebar
- **Search emails** - Use search bar at top
- **View email** - Click email in middle column
- **View attachments** - Click "View" for images, "Download" for files
- **Navigate** - Use Previous/Next buttons for pagination

### Configuration

Edit `.env` file:

```bash
# Port to expose
PORT=8080

# Path to mbox file (inside container)
MBOX_FILE=/data/emails.mbox

# Data directory
DATA_DIR=/data
```

## 🏗️ Architecture

### Technology Stack

- **Backend**: Python Flask
- **Database**: SQLite with FTS5 full-text search
- **Frontend**: Vanilla JavaScript, responsive CSS
- **Deployment**: Docker, Docker Compose

### How It Works

1. **Indexing**: Parses mbox file, extracts emails and attachments
2. **Storage**: Stores in SQLite with FTS5 search index
3. **Caching**: Database named by mbox file hash (reindexes only when file changes)
4. **Search**: FTS5 provides instant full-text search
5. **Attachments**: Stored as BLOBs in SQLite

### Performance

- **Indexing speed**: ~10-50 emails/second
- **Search speed**: < 50ms for any query
- **Page load**: < 100ms for 50 emails
- **Memory usage**: Low (SQLite handles storage)
- **Database size**: ~30-50% of original mbox size

## 📁 Project Structure

```
mbox-reader/
├── app.py                 # Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Container image
├── docker-compose.yml    # Docker Compose config
├── .env.example          # Configuration template
├── templates/
│   └── index.html        # Web interface
├── data/                 # Data directory (mounted)
│   ├── emails.mbox       # Your mbox file
│   ├── emails_*.db       # SQLite database (auto-generated)
│   └── progress_*.json   # Progress tracking (auto-generated)
└── README.md            # This file
```

## 🔧 Advanced Usage

### Multiple Mbox Files

Run multiple instances on different ports:

```bash
# Instance 1 - Personal
PORT=8080 MBOX_FILE=/data/personal.mbox docker-compose up -d

# Instance 2 - Work  
PORT=8081 MBOX_FILE=/data/work.mbox docker-compose up -d
```

### Local Development

Without Docker:

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export MBOX_PATH=./data/emails.mbox
export PORT=5000

# Run application
python app.py
```

### Backup Your Database

The SQLite database is much smaller than the mbox file:

```bash
# Backup database
cp data/emails_*.db backup/

# Restore database
cp backup/emails_*.db data/
```

## 🐛 Troubleshooting

### Indexing is slow
- Normal for large files (12GB = 30-60 minutes)
- Progress is saved, safe to restart
- Check terminal for "Indexed X emails" messages

### Search not working
- Click "Rebuild Index" to create FTS5 search index
- Check for "Search Index Not Available" error
- Rebuild creates the search index

### Attachments not showing
- Must rebuild database after updating code
- Attachments extracted during indexing
- Check browser console for errors

### Out of disk space
- Database is ~30-50% of mbox size
- Old databases not auto-deleted
- Manually remove `data/emails_*.db` files

## 🔒 Security Notes

- **Local use only** - No authentication implemented
- **Not for production** - Development server used
- **Private network** - Don't expose to internet
- **Your data** - Everything stays on your machine

## 📝 License

MIT License - See LICENSE file for details

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 💬 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/mbox-reader/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/mbox-reader/discussions)

## 🙏 Acknowledgments

Built to solve a real problem: reducing Google Workspace storage costs while maintaining access to email history.

---

**Made with ❤️ to save money on cloud storage**