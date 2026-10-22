import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

PDF_FILENAME = "ALGO_BOT_COMPLETE_PROJECT_DOCUMENTATION.pdf"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), PDF_FILENAME)


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print total page count."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 755, "Autonomous Leveraged ETF Trading Bot | Architecture & Setup Guide")
            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(54, 747, 558, 747)

        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 45, 558, 45)
        self.drawString(54, 32, "Confidential & Proprietary — Alpaca Algo-Trading Terminal")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 32, page_str)
        self.restoreState()


def build_pdf():
    doc = SimpleDocTemplate(
        OUTPUT_PATH,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    # Custom color palette
    c_primary = colors.HexColor("#0f172a")     # Deep Slate
    c_secondary = colors.HexColor("#1e293b")   # Slate 800
    c_accent = colors.HexColor("#2563eb")      # Blue
    c_green = colors.HexColor("#059669")       # Emerald
    c_red = colors.HexColor("#dc2626")         # Crimson
    c_text = colors.HexColor("#1e293b")
    c_muted = colors.HexColor("#64748b")
    c_bg_light = colors.HexColor("#f8fafc")
    c_border = colors.HexColor("#e2e8f0")

    # Typography styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=c_primary,
        spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=c_muted,
        spaceAfter=15
    )
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=19,
        textColor=c_primary,
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True
    )
    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=c_accent,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True
    )
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=c_text,
        spaceAfter=6
    )
    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=body_style,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=3
    )
    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#0f172a"),
        backColor=colors.HexColor("#f1f5f9"),
        borderPadding=6,
        spaceAfter=6
    )
    callout_style = ParagraphStyle(
        'Callout',
        parent=body_style,
        fontName='Helvetica-Oblique',
        textColor=colors.HexColor("#0369a1"),
        backColor=colors.HexColor("#f0f9ff"),
        borderColor=colors.HexColor("#bae6fd"),
        borderWidth=1,
        borderPadding=8,
        spaceAfter=8
    )

    story = []

    # -------------------------------------------------------------
    # COVER / HEADER SECTION
    # -------------------------------------------------------------
    story.append(Paragraph("Autonomous Leveraged ETF Trading Bot", title_style))
    story.append(Paragraph("Full Project Architecture, Mathematical Strategies & New Laptop Setup Guide", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_accent, spaceBefore=0, spaceAfter=14))

    # Executive Summary Card
    exec_summary_html = """
    <b>System Overview:</b> This autonomous quantitative trading system executes algorithmic momentum strategies across a 36-ETF universe of high-beta 2x and 3x leveraged bull and bear exchange-traded funds. It combines an institutional <b>Macro Cash Shield</b>, dynamic <b>ATR Trailing Stops</b>, <b>Profit-Tier Ratchets</b>, real-time <b>VADER News Sentiment Scoring</b> with automated <b>Long/Short Pair Switching</b>, and a live <b>Web Operations Cockpit (http://localhost:5000)</b> for monitoring and portfolio management.
    """
    story.append(Paragraph(exec_summary_html, callout_style))
    story.append(Spacer(1, 8))

    # -------------------------------------------------------------
    # SECTION 1: ARCHITECTURAL BREAKDOWN & FILE ROLES
    # -------------------------------------------------------------
    story.append(Paragraph("1. Project Architecture & File-by-File Breakdown", h1_style))
    story.append(Paragraph("The system is designed with strict modular separation between order execution, news intelligence, user interface, and backtesting harnesses:", body_style))

    file_data = [
        [Paragraph("<b>File Name</b>", body_style), Paragraph("<b>Primary Role & Core Functions</b>", body_style)],
        [
            Paragraph("<b>live_trader.py</b>", body_style),
            Paragraph("<b>Live Execution Engine</b>: Connects to Alpaca Paper/Live API using LumiBot. Evaluates candidate ETFs, executes daily champion rotation, tracks peak prices for trailing stops, enforces Macro Cash Shield, and triggers breaking news inverse switches.", body_style)
        ],
        [
            Paragraph("<b>news_sentiment_engine.py</b>", body_style),
            Paragraph("<b>AI News Intelligence</b>: Connects to Alpaca News API (Benzinga, Bloomberg, Dow Jones). Performs real-time VADER lexicon sentiment polarity scoring (-1.0 to +1.0). Maps 9 sector Long/Short pairs (e.g., SOXL <-> SOXS, TQQQ <-> SQQQ) and caches signals.", body_style)
        ],
        [
            Paragraph("<b>dashboard.py</b>", body_style),
            Paragraph("<b>Web Operations Cockpit (Port 5000)</b>: Built with native Python HTTP daemon and Vanilla CSS/JS. Displays account equity curve (Chart.js), open positions PnL, executed trades, sector sentiment pills, breaking headlines feed, single-click ticker modal, and emergency liquidate button.", body_style)
        ],
        [
            Paragraph("<b>run_champion_backtest.py</b>", body_style),
            Paragraph("<b>Historical Simulation Harness</b>: Matches live execution rules 1-to-1. Replays historical daily bars, tracks portfolio equity indicators, and outputs interactive QuantStats HTML tear sheets, indicators charts, and trade execution logs in logs/.", body_style)
        ],
        [
            Paragraph("<b>watchlist.json</b>", body_style),
            Paragraph("<b>Active Universe Registry</b>: Central synchronized JSON store holding the active candidate ETFs/stocks. Supports live dynamic additions and deletions from the Web UI modal without rebooting the bot.", body_style)
        ],
        [
            Paragraph("<b>.env</b>", body_style),
            Paragraph("<b>Secure Configuration</b>: Holds Alpaca API keys, secret tokens, paper trading toggles, and base URLs. Never committed to version control.", body_style)
        ],
    ]
    t_files = Table(file_data, colWidths=[140, 364])
    t_files.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ('GRID', (0, 0), (-1, -1), 0.5, c_border),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_files)
    story.append(Spacer(1, 12))

    # -------------------------------------------------------------
    # SECTION 2: QUANTITATIVE STRATEGIES & RISK CONTROLS
    # -------------------------------------------------------------
    story.append(Paragraph("2. Quantitative Strategies & Mathematical Risk Controls", h1_style))
    story.append(Paragraph("To eliminate the steep givebacks common to 2x/3x leveraged ETFs, the bot employs four layers of protective engineering:", body_style))

    story.append(Paragraph("<b>A. Macro Cash Shield (Regime Filter):</b>", h2_style))
    story.append(Paragraph("Checks the benchmark (SPY) against its 50-day Simple Moving Average. If SPY < 50 SMA, the bot instantly liquidates all equity positions and sits in <b>100% Cash</b>. In backtests, this kept capital safe for 42% of the year, preventing catastrophic market pullbacks.", bullet_style))

    story.append(Paragraph("<b>B. 20-Day SMA Trend & 10-Day Momentum (ROC) Ranking:</b>", h2_style))
    story.append(Paragraph("A candidate ETF must be trading above its 20-day SMA to qualify. Qualified candidates are ranked by 10-day Rate of Change: <code>ROC = (Current - Price_10d_ago) / Price_10d_ago</code>. Only the <b>Top 3 Champions</b> are bought, with ~28% allocated to each (84% total exposure, 16% safety cash).", bullet_style))

    story.append(Paragraph("<b>C. Dynamic ATR Trailing Stops & Profit-Tier Ratchet:</b>", h2_style))
    story.append(Paragraph("Volatility stops adapt using the 14-day Average True Range: <code>Stop_Dist = Clamp(3.5%, 6.0%, (1.8 * ATR_14) / Price)</code>.<br/>When a position surges parabolically, the <b>Profit Ratchet</b> tightens stops: at <b>+15% profit</b>, stop tightens to <b>3.0%</b>; at <b>+35% profit</b>, stop locks at <b>2.0%</b> from peak to exit right at market tops.", bullet_style))

    story.append(Paragraph("<b>D. Real-Time News Sentiment & Inverse Pair Switching:</b>", h2_style))
    story.append(Paragraph("The bot scans breaking wire headlines. If a sector's sentiment score turns severely bearish (&le; -0.18), the bot immediately dumps the Bull ETF and buys the corresponding <b>Inverse Bear ETF</b> (e.g., exits SOXL and buys SOXS) to profit while the sector crashes.", bullet_style))
    story.append(Spacer(1, 8))

    # Sector Pairs Table
    story.append(Paragraph("<b>Active Long / Short Sector Pairs:</b>", h2_style))
    pair_table_data = [
        [Paragraph("<b>Sector Domain</b>", body_style), Paragraph("<b>Bull ETF (Long)</b>", body_style), Paragraph("<b>Bear ETF (Inverse Short)</b>", body_style), Paragraph("<b>Underlying Proxy Tickers</b>", body_style)],
        [Paragraph("Semiconductors & AI", body_style), Paragraph("SOXL (3x)", body_style), Paragraph("SOXS (3x)", body_style), Paragraph("NVDA, AMD, TSM, INTC", body_style)],
        [Paragraph("Tech & Nasdaq 100", body_style), Paragraph("TQQQ (3x)", body_style), Paragraph("SQQQ (3x)", body_style), Paragraph("AAPL, MSFT, GOOGL, QQQ", body_style)],
        [Paragraph("S&P 500 Broad Market", body_style), Paragraph("UPRO (3x)", body_style), Paragraph("SPXU (3x)", body_style), Paragraph("SPY, VOO, Large-Cap Index", body_style)],
        [Paragraph("Financials & Banks", body_style), Paragraph("FAS (3x)", body_style), Paragraph("FAZ (3x)", body_style), Paragraph("JPM, BAC, XLF", body_style)],
        [Paragraph("Energy & Oil Exploration", body_style), Paragraph("GUSH (2x)", body_style), Paragraph("DRIP (2x)", body_style), Paragraph("XOP, CVX, XOM", body_style)],
        [Paragraph("Biotech & Genomics", body_style), Paragraph("LABU (3x)", body_style), Paragraph("LABD (3x)", body_style), Paragraph("XBI, IBB", body_style)],
        [Paragraph("Nvidia Ecosystem", body_style), Paragraph("NVDL (2x)", body_style), Paragraph("NVDD (2x)", body_style), Paragraph("NVDA single-stock leverage", body_style)],
        [Paragraph("Tesla & EV", body_style), Paragraph("TSLL (2x)", body_style), Paragraph("TSLS (2x)", body_style), Paragraph("TSLA single-stock leverage", body_style)],
    ]
    t_pairs = Table(pair_table_data, colWidths=[130, 80, 80, 214])
    t_pairs.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ('GRID', (0, 0), (-1, -1), 0.5, c_border),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t_pairs)

    story.append(PageBreak())

    # -------------------------------------------------------------
    # SECTION 3: STEP-BY-STEP NEW LAPTOP SETUP GUIDE
    # -------------------------------------------------------------
    story.append(Paragraph("3. New Laptop Installation & Setup Guide", h1_style))
    story.append(Paragraph("Follow this step-by-step checklist to transfer and run the complete trading system on any brand-new Windows, macOS, or Linux laptop.", body_style))

    story.append(Paragraph("Step 1: Install Python (Version 3.11 or 3.12 64-Bit)", h2_style))
    story.append(Paragraph("1. Download Python from <code>https://www.python.org/downloads/</code> (version 3.11 or 3.12 recommended).<br/>2. <b>CRITICAL (Windows):</b> During setup, check the box: <b>'Add python.exe to PATH'</b>.<br/>3. Open Command Prompt or PowerShell and verify: <code>python --version</code> and <code>pip --version</code>.", bullet_style))

    story.append(Paragraph("Step 2: Transfer / Clone the Project Folder", h2_style))
    story.append(Paragraph("Copy the project folder <code>my_algo_bot</code> to your preferred drive on the new laptop (e.g., <code>C:\\alpaca\\my_algo_bot</code> or <code>D:\\alpaca\\my_algo_bot</code>).", bullet_style))

    story.append(Paragraph("Step 3: Create an Isolated Python Virtual Environment", h2_style))
    story.append(Paragraph("Open terminal in the project directory and run:", body_style))
    cmd_box1 = """cd C:\\alpaca\\my_algo_bot
python -m venv venv"""
    story.append(Paragraph(cmd_box1.replace("\n", "<br/>"), code_style))

    story.append(Paragraph("Step 4: Activate the Virtual Environment", h2_style))
    story.append(Paragraph("<b>Windows (PowerShell):</b>", body_style))
    cmd_box2 = """.\\venv\\Scripts\\Activate.ps1
# If PowerShell blocks script execution, run this once:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser"""
    story.append(Paragraph(cmd_box2.replace("\n", "<br/>"), code_style))
    story.append(Paragraph("<b>macOS / Linux:</b>", body_style))
    story.append(Paragraph("source venv/bin/activate", code_style))

    story.append(Paragraph("Step 5: Install Required Dependencies", h2_style))
    story.append(Paragraph("Install the exact trading framework, financial analytics, and AI sentiment packages:", body_style))
    cmd_box3 = """pip install --upgrade pip
pip install -r requirements.txt
pip install nltk reportlab
python -c "import nltk; nltk.download('vader_lexicon')" """
    story.append(Paragraph(cmd_box3.replace("\n", "<br/>"), code_style))

    story.append(Paragraph("Step 6: Configure Environment Credentials (.env)", h2_style))
    story.append(Paragraph("Create or edit the <code>.env</code> file in the project root directory with your Alpaca paper API credentials:", body_style))
    env_box = """ALPACA_API_KEY="<your-alpaca-api-key>"
ALPACA_API_SECRET="<your-alpaca-api-secret>"
ALPACA_IS_PAPER="true"
APCA_API_BASE_URL="https://paper-api.alpaca.markets/v2" """
    story.append(Paragraph(env_box.replace("\n", "<br/>"), code_style))

    story.append(Paragraph("Step 7: Launch the Web Cockpit & Live Trader", h2_style))
    story.append(Paragraph("You can launch the Web Cockpit standalone or start the autonomous bot:", body_style))
    cmd_box4 = """# Option A: Start Web Dashboard only:
python dashboard.py
# -> Open your browser to http://localhost:5000

# Option B: Launch Live Autonomous Bot (automatically boots Dashboard on port 5000):
python live_trader.py"""
    story.append(Paragraph(cmd_box4.replace("\n", "<br/>"), code_style))

    story.append(Paragraph("Step 8: Run Historical Backtests", h2_style))
    story.append(Paragraph("To simulate and evaluate performance over custom date ranges (e.g., previous 1-year or multi-year windows):", body_style))
    cmd_box5 = """python run_champion_backtest.py
# -> Generates interactive HTML tear sheets in the logs/ folder."""
    story.append(Paragraph(cmd_box5.replace("\n", "<br/>"), code_style))

    story.append(Spacer(1, 10))

    # -------------------------------------------------------------
    # SECTION 4: FREQUENTLY ASKED QUESTIONS & TROUBLESHOOTING
    # -------------------------------------------------------------
    story.append(Paragraph("4. Troubleshooting & Operational FAQ", h1_style))
    
    faq_items = [
        ("Port 5000 Already in Use?", "If another background process occupies port 5000, find and terminate it via PowerShell:<br/><code>Get-NetTCPConnection -LocalPort 5000 | Select-Object -ExpandProperty OwningProcess | Stop-Process -Force</code>"),
        ("Market CLOSED / Bot Sleeping?", "US Equity and Leveraged ETF exchanges trade 9:30 AM – 4:00 PM EST, Monday through Friday. Outside these hours, the bot sleeps to prevent high slippage and illiquid execution spreads."),
        ("Does the Bot Buy All Watchlist Stocks?", "No. The watchlist serves as candidate radar. The bot filters candidates by trend (20 SMA) and sentiment, then purchases <b>only the Top 3 Champions</b> (~28% each), keeping the remaining funds in cash reserve."),
        ("How to Add New Tickers on the Fly?", "Open <code>http://localhost:5000</code> in your browser and click <b>'+ Add Stock Ticker'</b>. Type any ticker (e.g. <code>NVDA</code>, <code>TQQQ</code>, <code>AAPL</code>) and submit. The system updates <code>watchlist.json</code> instantly with duplicate protection."),
    ]
    for q, a in faq_items:
        story.append(Paragraph(f"<b>Q: {q}</b>", h2_style))
        story.append(Paragraph(a, bullet_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated PDF documentation at: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_pdf()
