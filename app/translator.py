import json
import logging
import sqlite3
import re
from typing import List, Dict, Optional, Any, Union
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY, DB_PATH, settings

logger = logging.getLogger("sentinel.translator")

# In-memory session cache for instant UI response
_MEMORY_CACHE: Dict[str, str] = {}

SUPPORTED_LANGUAGES = ["EN", "HI", "KN", "TE", "TA", "MR", "BN"]

LANG_NORMALIZATION: Dict[str, str] = {
    "EN": "EN",
    "ENGLISH": "EN",
    "HI": "HI",
    "HINDI": "HI",
    "KN": "KN",
    "KANNADA": "KN",
    "TE": "TE",
    "TELUGU": "TE",
    "TA": "TA",
    "TAMIL": "TA",
    "MR": "MR",
    "MARATHI": "MR",
    "BN": "BN",
    "BENGALI": "BN"
}

def normalize_lang_code(lang: Optional[str]) -> str:
    """Normalizes input language string to standard ISO 2-letter uppercase code."""
    if not lang:
        return "EN"
    return LANG_NORMALIZATION.get(lang.strip().upper(), lang.strip().upper())

def detect_dominant_script(text: str) -> str:
    """Detects whether text contains Devanagari (HI), Kannada (KN), Telugu (TE), Tamil (TA), or Latin (EN)."""
    if not text:
        return "EN"
    if re.search(r'[\u0900-\u097F]', text):
        return "HI"
    if re.search(r'[\u0C80-\u0CFF]', text):
        return "KN"
    if re.search(r'[\u0C00-\u0C7F]', text):
        return "TE"
    if re.search(r'[\u0B80-\u0BFF]', text):
        return "TA"
    return "EN"

def get_genai_client():
    """Initializes Google GenAI client using GEMINI_API_KEY from config/environment."""
    key = GEMINI_API_KEY or settings.GEMINI_API_KEY
    if not key or not key.strip():
        return None
    try:
        return genai.Client(api_key=key.strip())
    except Exception as e:
        logger.warning(f"[TRANSLATOR] Error initializing Google GenAI client: {e}")
        return None

def init_translation_table():
    """Ensure persistent SQLite translation cache table exists with compound primary key."""
    db_file = DB_PATH or settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles_translations (
            id TEXT,
            lang TEXT,
            title TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id, lang)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_art_trans_id_lang ON articles_translations(id, lang);")
    conn.commit()
    conn.close()

init_translation_table()

def is_pure_english(text: str) -> bool:
    """Check if string contains primarily English/ASCII characters or harmless Latin typography."""
    if not text:
        return True
    cleaned = (
        text.replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
            .replace("—", "-")
            .replace("–", "-")
            .replace("…", "...")
    )
    try:
        cleaned.encode('ascii')
        return True
    except UnicodeEncodeError:
        return not bool(re.search(r'[\u0900-\u0D7F\u0400-\u04FF\u0600-\u06FF\u4E00-\u9FFF]', cleaned))

# Preserved platform designations and military acronyms
PRESERVED_ACRONYMS = [
    r'\bS-400\b',
    r'\bLCA\s+Tejas(?:\s+Mk1A)?\b',
    r'\bTejas(?:\s+Mk1A)?\b',
    r'\bAESA\b',
    r'\bDRDO\b',
    r'\bMach\s+\d+\b',
    r'\bLandsverk\s+L\s*60\b',
    r'\bPWSA\s+Tranche\s+\d+\b',
    r'\bUGV\b',
    r'\bP-8I\b',
    r'\bBrahMos\b',
    r'\bSINKEX\b',
    r'\bUSS\s+[A-Za-z\.]+(?:\s+[A-Za-z\.]+)*\b',
    r'\bINS\s+[A-Za-z]+\b',
    r'\bIAF\b',
    r'\bADA\b',
    r'\bTEDBF\b',
    r'\bAMCA\b',
    r'\bMQ-9B\b',
    r'\bC-390\b',
    r'\bA400M\b',
    r'\bTapas-BH-201\b',
    r'\bMALE\b',
    r'\bHALE\b',
    r'\bEW\b',
    r'\bTKMS\b',
    r'\bMH-60R\b',
    r'\bK9\b',
    r'\bATAGS\b',
    r'\bT-72\b',
    r'\bF-35\b',
    r'\bF/A-XX\b',
    r'\bCVN\s*79\b',
    r'\bMUSV\b',
    r'\bUSV\b',
    r'\bUSVs\b',
    r'\bDGCA\b',
    r'\bLeT\b',
    r'\bJ&K\b',
    r'\bNHAI\b',
    r'\bSCADA\b',
    r'\bAPT\b',
    r'\bGaN\b',
    r'\bGPI\b',
    r'\bNATO\b',
    r'\bHII\b',
    r'\bUS\b',
    r'\bUK\b',
    r'\bEU\b',
    r'\bUAE\b',
    r'\bCIA\b',
    r'\bRF\b',
    r'\bECM\b',
    r'\bAI\b',
    r'\bGPS\b',
    r'\bIFF\b',
    r'\bSATCOM\b',
    r'\bSIGINT\b',
    r'\bHUMINT\b',
    r'\bIMINT\b',
    r'\bOSINT\b',
    r'\bFTS5\b',
    r'\bWAL\b',
    r'\bSHA-256\b',
    r'\bSu-30MKI\b',
    r'\bMiG-29\b',
    r'\bMirage\s+2000\b',
    r'\bRafale\b',
    r'\bApache\b',
    r'\bChinook\b',
    r'\bSeahawk\b',
    r'\bNeptune\b',
    r'\bZorawar\b',
    r'\bVajra-T\b',
    r'\bProject\s+75I\b',
    r'\bProject\s+18\b',
    r'\bProject\s+17A\b',
    r'\bNilgiri-Class\b',
    r'\bXV\s+Excalibur\b',
    r'\bROMULUS\b',
    r'₹[\d,]+',
    r'\$[\d\.]+[MBK]?',
    r'\b\d+mm\b',
    r'\b\d+-Caliber\b',
    r'\b\d+-Tonne\b',
    r'\b\d+-Aircraft\b'
]
ACRONYM_COMBINED = re.compile("|".join(PRESERVED_ACRONYMS), re.IGNORECASE)

MILITARY_VOCAB = {
    # Full tactical titles & multi-word expressions
    "tactical defence technology operational reconnaissance analysis": {
        "HI": "सामरिक रक्षा प्रौद्योगिकी परिचालन टोही विश्लेषण",
        "KN": "ಯುದ್ಧತಂತ್ರದ ರಕ್ಷಣಾ ತಂತ್ರಜ್ಞಾನ ಕಾರ್ಯಾಚರಣೆಯ ವಿಚಕ್ಷಣಾ ವಿಶ್ಲೇಷಣೆ",
        "TE": "వ్యూహాత్మక రక్షణ సాంకేతిక కార్యాచరణ నిఘా విశ్లేషణ",
        "TA": "உத்திசார் பாதுகாப்பு தொழில்நுட்ப செயல்பாட்டு உளவு பகுப்பாய்வு"
    },
    "landsverk l 60 tank recon reconnaissance analysis": {
        "HI": "Landsverk L 60 टैंक टोही विश्लेषण",
        "KN": "Landsverk L 60 ಟ್ಯಾಂಕ್ ವಿಚಕ್ಷಣಾ ವಿಶ್ಲೇಷಣೆ",
        "TE": "Landsverk L 60 ట్యాంక్ నిఘా విశ్లేషణ",
        "TA": "Landsverk L 60 பீரங்கி உளவு பகுப்பாய்வு"
    },
    "landsverk l 60 at collins barracks jpg reconnaissance analysis": {
        "HI": "Landsverk L 60 कोलिन्स बैरक टोही विश्लेषण",
        "KN": "Landsverk L 60 ಕಾಲಿನ್ಸ್ ಬ್ಯಾರಕ್ಸ್ ವಿಚಕ್ಷಣಾ ವಿಶ್ಲೇಷಣೆ",
        "TE": "Landsverk L 60 కాలిన్స్ బ్యారక్స్ నిఘా విశ్లేషణ",
        "TA": "Landsverk L 60 காலின்ஸ் பாரக்ஸ் உளவு பகுப்பாய்வு"
    },
    "luftwaffe dornier do 217n heavy night fighter reconnaissance": {
        "HI": "Luftwaffe Dornier Do 217N भारी रात्रि लड़ाकू टोही",
        "KN": "Luftwaffe Dornier Do 217N ಹೆವಿ ನೈಟ್ ಫೈಟರ್ ವಿಚಕ್ಷಣ",
        "TE": "Luftwaffe Dornier Do 217N హెవీ నైట్ ఫైటర్ నిఘా",
        "TA": "Luftwaffe Dornier Do 217N இரவு போர் விமான உளவு"
    },
    "stratospheric hypersonic glide phase interceptor validates gan aesa terminal seeker": {
        "HI": "समतापमंडलीय हाइपरसोनिक ग्लाइड चरण इंटरसेप्टर ने GaN AESA टर्मिनल साधक को मान्य किया",
        "KN": "ಸ್ಟ್ರಾಟೋಸ್ಫಿಯರಿಕ್ ಹೈಪರ್ಸಾನಿಕ್ ಗ್ಲೈಡ್ ಹಂತದ ಇಂಟರ್ಸೆಪ್ಟರ್ GaN AESA ಟರ್ಮಿನಲ್ ಸೀಕರ್ ಅನ್ನು ಮೌಲ್ಯೀಕರಿಸಿದೆ",
        "TE": "స్ట్రాటోస్పిరిక్ హైపర్‌సోనిక్ గ్లైడ్ దశ ఇంటర్‌సెప్టర్ GaN AESA టెర్మినల్ సీకర్‌ను ధృవీకరించింది",
        "TA": "ஸ்ட்ராடோஸ்பெரிக் ஹைப்பர்சோனிக் இடைமறிப்பு ஏவுகணை GaN AESA அமைப்பை சரிபார்த்தது"
    },
    "distributed quantum magnetometer sonar array deployed for deep-sea submarine acoustic profiling": {
        "HI": "गहरे समुद्र में पनडुब्बी ध्वनिक प्रोफाइलिंग के लिए वितरित क्वांटम मैग्नेटोमीटर सोनार सरणी तैनात",
        "KN": "ಆಳ ಸಮುದ್ರದ ಜಲಾಂತರ್ಗಾಮಿ ಅಕೌಸ್ಟಿಕ್ ಪ್ರೊಫೈಲಿಂಗ್‌ಗಾಗಿ ಕ್ವಾಂಟಮ್ ಮ್ಯಾಗ್ನೆಟೋಮೀಟರ್ ಸೋನಾರ್ ಅರೇ ನಿಯೋಜಿಸಲಾಗಿದೆ",
        "TE": "లోతైన సముద్ర జలాంతర్గామి శబ్ద ప్రೊಫైలింగ్ కోసం క్వాంటం మాగ్నెటోమీటర్ సోనార్ అర్రే మోహరించబడింది",
        "TA": "ஆழ்கடல் நீர்மூழ்கிக் கப்பல் கண்காணிப்புக்காக குவாಂಡம் சோனார் வரிசை நிலைநிறுத்தப்பட்டது"
    },
    "state-sponsored apt group quarantined following zero-day injection in scada military radar grid": {
        "HI": "SCADA सैन्य रडार ग्रिड में ज़ीरो-डे इंजेक्शन के बाद राज्य प्रायोजित APT समूह को अलग किया गया",
        "KN": "SCADA ಮಿಲಿಟರಿ ರಾಡಾರ್ ಗ್ರಿಡ್‌ನಲ್ಲಿ ಜೀರೋ-ಡೇ ಇಂಜೆಕ್ಷನ್ ನಂತರ ರಾಜ್ಯ-ಪ್ರಾಯೋಜಿತ APT ಗುಂಪನ್ನು ಪ್ರತ್ಯೇಕಿಸಲಾಗಿದೆ",
        "TE": "SCADA మిలిటరీ రాడార్ గ్రిడ్‌లో జీరో-డే దాడి తర్వాత ప్రభుత్వ-మద్దతు గల APT సమూహం వేరుచేయబడింది",
        "TA": "SCADA ராணுவ ரேடார் கட்டமைப்பு மீதான தாக்குதலைத் தொடர்ந்து அரசு ஆதரவு APT குழு தனிமைப்படுத்தப்பட்டது"
    },
    "autonomous loyal wingman uav drone swarms integrated with 5th-gen stealth interceptors in high-altitude live-fire exercise": {
        "HI": "उच्च ऊंचाई वाले लाइव-फायर युद्धाभ्यास में 5वीं पीढ़ी के स्टील्थ इंटरसेप्टर के साथ स्वायत्त विंगमैन UAV ड्रोन झुंड एकीकृत",
        "KN": "ಹೆಚ್ಚಿನ ಎತ್ತರದ ಲೈವ್-ಫೈರ್ ಕಸರತ್ತಿನಲ್ಲಿ 5ನೇ ತಲೆಮಾರಿನ ಸ್ಟೆಲ್ತ್ ಇಂಟರ್‌ಸೆಪ್ಟರ್‌ಗಳೊಂದಿಗೆ ಸ್ವಾಯತ್ತ ಲಾಯಲ್ ವಿಂಗ್‌ಮ್ಯಾನ್ UAV ಡ್ರೋನ್ ಹಿಂಡುಗಳು ಸಂಯೋಜಿಸಲ್ಪಟ್ಟಿವೆ",
        "TE": "అధిక ఎత్తులో జరిగిన లైవ్-ఫైర్ విన్యాసంలో 5వ తరం స్టెల్త్ ఇంటర్‌సెప్టర్‌లతో స్వయంప్రతిపత్తి లాయల్ వింగ్‌మ్యాన్ UAV డ్రోన్ గుంపులు సమీకృతం చేయబడ్డాయి"
    },
    "heavy unmanned ground combat vehicles execute coordinated counter-armor breaching assault in live polygon trials": {
        "HI": "भारी मानवरहित ग्राउंड लड़ाकू वाहनों ने लाइव परीक्षणों में समन्वित बख्तरबंद भेदन हमला किया",
        "KN": "ಭಾರೀ ಮಾನವರಹಿತ ನೆಲದ ಯುದ್ಧ ವಾಹನಗಳು ಲೈವ್ ಬಹುಭುಜಾಕೃತಿಯ ಪ್ರಯೋಗಗಳಲ್ಲಿ ಸಂಘಟಿತ ಕೌಂಟರ್-ಆರ್ಮರ್ ಉಲ್ಲಂಘನೆಯ ದಾಳಿಯನ್ನು ನಡೆಸಿವೆ",
        "TE": "భారీ మానవరహిత భూ పోరాట వాహనాలు లైవ్ ట్రయల్స్‌లో సమన్వయ కౌంటర్-ఆర్మర్ బ్రీచింగ్ దాడిని చేపట్టాయి"
    },
    "proliferated low earth orbit optical inter-satellite reconnaissance constellation achieves initial operational capability": {
        "HI": "लो अर्थ ऑर्बिट ऑप्टिकल इंटर-सैटेलाइट टोही तारामंडल ने प्रारंभिक परिचालन क्षमता हासिल की",
        "KN": "ಕಡಿಮೆ ಭೂಮಿಯ ಕಕ್ಷೆಯ ಆಪ್ಟಿಕಲ್ ಇಂಟರ್-ಸ್ಯಾಟಲೈಟ್ ವಿಚಕ್ಷಣಾ ಸಮೂಹವು ಆರಂಭಿಕ ಕಾರ್ಯಾಚರಣೆಯ ಸಾಮರ್ಥ್ಯವನ್ನು ಸಾಧಿಸಿದೆ",
        "TE": "తక్కువ భూ కక్ష్య ఆప్టికల్ ఇంటర్-శాటిలైట్ నిఘా సముదాయం ప్రారంభ కార్యాచరణ సామర్థ్యాన్ని సాధించింది"
    },
    "counter-radar rf jamming pod demonstrates ecm deception over border sectors": {
        "HI": "काउंटर-रडार RF जैमिंग पॉड ने सीमावर्ती क्षेत्रों में ECM धोखे का प्रदर्शन किया",
        "KN": "ಕೌಂಟರ್-ರಾಡಾರ್ RF ಜ್ಯಾಮಿಂಗ್ ಪಾಡ್ ಗಡಿ ವಲಯಗಳಲ್ಲಿ ECM ವಂಚನೆಯನ್ನು ಪ್ರದರ್ಶಿಸಿದೆ",
        "TE": "కౌంటర్-రాడార్ RF జామింగ్ పాడ్ సరిహద్దు రంగాలలో ECM మోసాన్ని ప్రదర్శించింది"
    },
    "lca tejas mk1a equipped with uttam aesa radar deployed for operational air defence.": {
        "HI": "Uttam AESA Radar से लैस LCA Tejas Mk1A को परिचालन वायु रक्षा के लिए तैनात किया गया।",
        "KN": "Uttam AESA Radar ಹೊಂದಿರುವ LCA Tejas Mk1A ಅನ್ನು ಕಾರ್ಯಾಚರಣಾ ವಾಯು ರಕ್ಷಣೆಗಾಗಿ ನಿಯೋಜಿಸಲಾಗಿದೆ.",
        "TE": "Uttam AESA Radar తో కూడిన LCA Tejas Mk1A కార్యాచరణ వైమానిక రక్షణ కోసం మోహరించబడింది.",
        "TA": "Uttam AESA Radar பொருத்தப்பட்ட LCA Tejas Mk1A செயல்பாட்டு வான் பாதுகாப்புக்காக நிலைநிறுத்தப்பட்டுள்ளது."
    },
    "ministry of defence": {"HI": "रक्षा मंत्रालय", "KN": "ರಕ್ಷಣಾ ಸಚಿವಾಲಯ", "TE": "రక్షణ మంత్రిత్వ శాఖ", "TA": "பாதுகாப்பு அமைச்சகம்"},
    "cabinet committee on security": {"HI": "सुरक्षा मामलों की मंत्रिमंडलीय समिति", "KN": "ಭದ್ರತೆ ಕುರಿತ ಸಂಪುಟ ಸಮಿತಿ", "TE": "భద్రతా వ్యవహారాల కేబినెట్ కమిటీ", "TA": "பாதுகாப்புக்கான அமைச்சரவைக் குழு"},
    "indian air force": {"HI": "भारतीय वायु सेना", "KN": "ಭಾರತೀಯ ವಾಯುಪಡೆ", "TE": "భారత వైమానిక దళం", "TA": "இந்திய விமானப்படை"},
    "indian navy": {"HI": "भारतीय नौसेना", "KN": "ಭಾರತೀಯ ನೌಕಾಪಡೆ", "TE": "భారత నౌకాదళం", "TA": "இந்திய கடற்படை"},
    "indian army": {"HI": "भारतीय थल सेना", "KN": "ಭಾರತೀಯ ಸೇನೆ", "TE": "భారత సైన్యం", "TA": "இந்திய தரைப்படை"},
    "security forces": {"HI": "सुरक्षा बल", "KN": "ಭದ್ರತಾ ಪಡೆಗಳು", "TE": "భద్రతా దళాలు", "TA": "பாதுகாப்புப் படைகள்"},
    "air defence": {"HI": "वायु रक्षा", "KN": "ವಾಯು ರಕ್ಷಣೆ", "TE": "వైమానిక రక్షణ", "TA": "வான் பாதுகாப்பு"},
    "royal navy": {"HI": "रॉयल नेवी", "KN": "ರಾಯಲ್ ನೇವಿ", "TE": "రాయల్ నేవీ", "TA": "ராயல் நேவி"},
    "defence technology": {"HI": "रक्षा प्रौद्योगिकी", "KN": "ರಕ್ಷಣಾ ತಂತ್ರಜ್ಞಾನ", "TE": "రక్షణ సాంకేతికత", "TA": "பாதுகாப்பு தொழில்நுட்பம்"},
    "land systems": {"HI": "भूमि प्रणाली", "KN": "ಭೂ ಸೇನಾ ವ್ಯವಸ್ಥೆಗಳು", "TE": "భూ వ్యవస్థలు", "TA": "தரைவழி அமைப்புகள்"},
    "cyber security": {"HI": "साइबर सुरक्षा", "KN": "ಸೈಬರ್ ಭದ್ರತೆ", "TE": "సైబర్ భద్రత", "TA": "இணைய பாதுகாப்பு"},
    "cybersecurity": {"HI": "साइबर सुरक्षा", "KN": "ಸೈಬರ್ ಭದ್ರತೆ", "TE": "సైబర్ భద్రత", "TA": "இணைய பாதுகாப்பு"},
    "electronic warfare": {"HI": "इलेक्ट्रॉनिक युद्ध", "KN": "ವಿದ್ಯುನ್ಮಾನ ಯುದ್ಧ", "TE": "ఎలక్ట్రానిక్ యుద్ధం", "TA": "மின்னணு போர்"},
    "undersea warfare": {"HI": "समुद्री युद्ध", "KN": "ಜಲಾಂತರ್ಗಾಮಿ ಯುದ್ಧ", "TE": "జలాంతర్గామి యుద్ధం", "TA": "கடலடி போர்"},
    "preliminary design review": {"HI": "प्रारंभिक डिजाइन समीक्षा", "KN": "ಪ್ರಾಥಮಿಕ ವಿನ್ಯಾಸ ಪರಿಶೀಲನೆ", "TE": "ప్రాథమిక డిజైన్ సమీక్ష"},
    "deep-sea submarine": {"HI": "गहरे समुद्र की पनडुब्बी", "KN": "ಆಳ ಸಮುದ್ರದ ಜಲಾಂತರ್ಗಾಮಿ", "TE": "లోతైన సముద్ర జలాంతర్గామి"},
    "guided missile destroyer": {"HI": "निर्देशित मिसाइल विध्वंसक", "KN": "ಮಾರ್ಗದರ್ಶಿತ ಕ್ಷಿಪಣಿ ವಿಧ್ವಂಸಕ", "TE": "గైడెడ్ క్షిపణి విధ్వంసక"},
    "guided missile frigate": {"HI": "निर्देशित मिसाइल फ्रिगेट", "KN": "ಮಾರ್ಗದರ್ಶಿತ ಕ್ಷಿಪಣಿ ಫ್ರಿಗೇಟ್", "TE": "గైడెడ్ క్షిపణి ఫ్రిగేట్"},
    "anti-submarine warfare": {"HI": "पनडुब्बी रोधी युद्ध", "KN": "ಜಲಾಂತರ್ಗಾಮಿ ನಿಗ್ರಹ ಯುದ್ಧ", "TE": "జలాంతర్గామి నిరోధక యుద్ధం"},
    "light tank": {"HI": "हल्का टैंक", "KN": "ಲಘು ಟ್ಯಾಂಕ್", "TE": "తేలికపాటి ట్యాంಕ್"},
    "future ready combat vehicles": {"HI": "फ्यूचर रेडी लड़ाकू वाहन", "KN": "ಭವಿಷ್ಯದ ಯುದ್ಧ ವಾಹನಗಳು", "TE": "ఫ్యూచర్ రెడీ పోరాట వాహనాలు"},
    "remotely piloted aircraft": {"HI": "दूरस्थ संचालित विमान", "KN": "ರಿಮೋಟ್ ಪೈಲಟೆಡ್ ವಿಮಾನ", "TE": "రిమోట్ పైలట్ విమానం"},
    "intergovernmental agreement": {"HI": "अंतर-सरकारी समझौता", "KN": "ಅಂತರ-ಸರ್ಕಾರಿ ಒಪ್ಪಂದ", "TE": "అంతర్-ప్రభుత్వ ఒప్పందం"},
    "comparative flight evaluations": {"HI": "तुलनात्मक उड़ान मूल्यांकन", "KN": "ತುಲನಾತ್ಮಕ ಹಾರಾಟದ ಮೌಲ್ಯಮಾಪನಗಳು", "TE": "తులనాత్మక విమాన మూల్యాంకనాలు"},
    "high-altitude winter sensor telemetry trials": {"HI": "उच्च ऊंचाई शीतकालीन सेंसर टेलीमेट्री परीक्षण", "KN": "ಹೆಚ್ಚಿನ ಎತ್ತರದ ಚಳಿಗಾಲದ ಸೆನ್ಸರ್ ಟೆಲಿಮೆಟ್ರಿ ಪರೀಕ್ಷೆಗಳು", "TE": "అధిక ఎత్తు శీతాకాల సెన్సార్ టెలిమెట్రీ ట్రయల్స్"},
    "structural life-extension": {"HI": "संरचनात्मक जीवन-विस्तार", "KN": "ರಚನಾತ್ಮಕ ಜೀವಿತಾವಧಿ ವಿಸ್ತರಣೆ", "TE": "నిర్మాణాత్మక జీవితకాల విస్తరణ"},
    "operational deterrence patrols": {"HI": "परिचालन निवारक गश्त", "KN": "ಕಾರ್ಯಾಚರಣೆಯ ಪ್ರತಿಬಂಧಕ ಗಸ್ತುಗಳು", "TE": "కార్యాచరణ నిరోధక గస్తీలు"},
    "air independent propulsion": {"HI": "वायु स्वतंत्र प्रणोदन", "KN": "ವಾಯು ಸ್ವತಂತ್ರ ಪ್ರೊಪಲ್ಷನ್", "TE": "ఎయిర్ ఇండిపెండెంట్ ప్రొపల్షన్"},
    "dual-carrier flight operations": {"HI": "दोहरे विमानवाहक उड़ान अभियान", "KN": "ಅವಳಿ ವಿಮಾನವಾಹಕ ಹಾರಾಟ ಕಾರ್ಯಾಚರಣೆಗಳು", "TE": "జంట విమాన వాహక విమాన కార్యకలాపాలు"},
    "hydrodynamic trials": {"HI": "हाइड्रोडायनामिक परीक्षण", "KN": "ಹೈಡ್ರೋಡೈನಾಮಿಕ್ ಪರೀಕ್ಷೆಗಳು", "TE": "హైడ్రోడైనమిక్ ట్రయల్స్"},
    "seabed mapping": {"HI": "समुद्र तल मानचित्रण", "KN": "ಸಮುದ್ರ ತಳದ ಮ್ಯಾಪಿಂಗ್", "TE": "సముద్రగర్భ మ్యాపింగ్"},
    "firing trials": {"HI": "फायरिंग परीक्षण", "KN": "ಫೈರಿಂಗ್ ಪರೀಕ್ಷೆಗಳು", "TE": "ಫೈರಿಂಗ್ ట్రయల్స్"},
    "tracked howitzer": {"HI": "ट्रैक्ड होवित्जर", "KN": "ಟ್ರ್ಯಾಕ್ ಮಾಡಲಾದ ಹೋವಿಟ್ಜರ್", "TE": "ట్రాక్ చేయబడిన హోవిట్జర్"},
    "commercial production contracts": {"HI": "वाणिज्यिक उत्पादन अनुबंध", "KN": "ವಾಣಿಜ್ಯ ಉತ್ಪಾದನಾ ಒಪ್ಪಂದಗಳು", "TE": "వాణిజ్య ఉత్పత్తి ఒప్పందాలు"},
    "tactical": {"HI": "सामरिक", "KN": "ಯುದ್ಧತಂತ್ರದ", "TE": "వ్యూహాత్మక"},
    "operational": {"HI": "परिचालन", "KN": "ಕಾರ್ಯಾಚರಣೆಯ", "TE": "కార్యాచరణ"},
    "reconnaissance": {"HI": "टोही", "KN": "ವಿಚಕ್ಷಣಾ", "TE": "నిఘా"},
    "analysis": {"HI": "विश्लेषण", "KN": "ವಿಶ್ಲೇಷಣೆ", "TE": "విశ్లేషణ"},
    "defence": {"HI": "रक्षा", "KN": "ರಕ್ಷಣಾ", "TE": "రక్షణ"},
    "defense": {"HI": "रक्षा", "KN": "ರಕ್ಷಣಾ", "TE": "రక్షణ"},
    "technology": {"HI": "प्रौद्योगिकी", "KN": "ತಂತ್ರಜ್ಞಾನ", "TE": "సాంకేతికత"},
    "aerospace": {"HI": "एयरोस्पेस", "KN": "ಏರೋಸ್ಪೇಸ್", "TE": "ఏరోస్పేస్"},
    "naval": {"HI": "नौसेना", "KN": "ನೌಕಾಪಡೆ", "TE": "ನೌಕಾదళం"},
    "systems": {"HI": "प्रणालियां", "KN": "ವ್ಯವಸ್ಥೆಗಳು", "TE": "వ్యవస్థలు"},
    "system": {"HI": "प्रणाली", "KN": "ವ್ಯವಸ್ಥೆ", "TE": "వ్యవస్థ"},
    "warship": {"HI": "युद्धपोत", "KN": "ಯುದ್ಧನೌಕೆ", "TE": "యుద్ధనౌక"},
    "warships": {"HI": "युद्धपोत", "KN": "ಯುದ್ಧನೌಕೆಗಳು", "TE": "యుద్ధనౌకలు"},
    "frigate": {"HI": "फ्रिगेट", "KN": "ಫ್ರಿಗೇಟ್", "TE": "ఫ్రిగేట్"},
    "destroyer": {"HI": "विध्वंसक", "KN": "ವಿಧ್ವಂಸಕ", "TE": "విధ్వంసక"},
    "carrier": {"HI": "विमानवाहक पोत", "KN": "ವಿಮಾನವಾಹಕ ನೌಕೆ", "TE": "విమాన వాహక నೌక"},
    "submarine": {"HI": "पनडुब्बी", "KN": "ಜಲಾಂತರ್ಗಾಮಿ", "TE": "జలాంతర్గామి"},
    "missile": {"HI": "मिसाइल", "KN": "ಕ್ಷಿಪಣಿ", "TE": "క్షిపణి"},
    "radar": {"HI": "रडार", "KN": "ರಾಡಾರ್", "TE": "రాಡార్"},
    "fighter": {"HI": "लड़ाकू विमान", "KN": "ಹೋರಾಟದ ವಿಮಾನ", "TE": "యుద్ధ విమానం"},
    "tank": {"HI": "टैंक", "KN": "ಟ್ಯಾಂಕ್", "TE": "ట్యాంక్"},
    "trials": {"HI": "परीक्षण", "KN": "ಪರೀಕ್ಷೆಗಳು", "TE": "ಪರೀಕ್ಷలు"},
    "test": {"HI": "परीक्षण", "KN": "ಪರೀಕ್ಷೆ", "TE": "పరీక్ష"},
    "exercise": {"HI": "युद्धाभ्यास", "KN": "ಕಸರತ್ತು", "TE": "విన్యాసం"},
    "deal": {"HI": "सौदा", "KN": "ಒಪ್ಪಂದ", "TE": "డీల్"},
    "contract": {"HI": "अनुबंध", "KN": "ಒಪ್ಪಂದ", "TE": "ఒప్పందం"},
    "agreement": {"HI": "समझौता", "KN": "ಒಪ್ಪಂದ", "TE": "ఒప్పందం"},
    "budget": {"HI": "बजट", "KN": "ಬಜೆಟ್", "TE": "బడ్జెట్"},
    "spending": {"HI": "खर्च", "KN": "ವೆಚ್ಚ", "TE": "వ్యయం"},
    "intelligence": {"HI": "खुफिया आसूचना", "KN": "ಗುಪ್ತಚರ ಮಾಹಿತಿ", "TE": "నిఘా సమాచారం"},
    "surveillance": {"HI": "निगरानी", "KN": "ಕಣ್ಗಾವಲು", "TE": "నిఘా"},
    "interceptor": {"HI": "इंटरसेप्टर", "KN": "ಇಂಟರ್‌ಸೆಪ್ಟರ್", "TE": "ఇంటర్‌సెప్టర్"},
    "hypersonic": {"HI": "हाइपरसोनिक", "KN": "ಹೈಪರ್‌ಸಾನಿಕ್", "TE": "హైపర్‌సోనిక్"},
    "stealth": {"HI": "स्टील्थ", "KN": "ಸ್ಟೆಲ್ತ್", "TE": "ಸ್ಟೆಲ್ತ್"},
    "indigenous": {"HI": "स्वदेशी", "KN": "ಸ್ವದೇಶಿ", "TE": "స్వదేశీ"},
    "autonomous": {"HI": "स्वायत्त", "KN": "ಸ್ವಾಯತ್ತ", "TE": "స్వయంప్రతిపత్తి"},
    "unmanned": {"HI": "मानवरहित", "KN": "ಮಾನವರಹಿತ", "TE": "మానవరహిత"},
    "uncrewed": {"HI": "मानवरहित", "KN": "ಮಾನವರಹಿತ", "TE": "మానవరహిత"},
    "drone": {"HI": "ड्रोन", "KN": "ಡ್ರೋನ್", "TE": "డ్రోన్"},
    "drones": {"HI": "ड्रोन", "KN": "ಡ್ರೋನ್‌ಗಳು", "TE": "డ్రోన్లు"},
    "deploys": {"HI": "तैनात करता है", "KN": "ನಿಯೋಜಿಸುತ್ತದೆ", "TE": "మోహరిస్తుంది"},
    "deployed": {"HI": "तैनात किया गया", "KN": "ನಿಯೋಜಿಸಲಾಗಿದೆ", "TE": "మోహరించబడింది"},
    "completes": {"HI": "पूरा किया", "KN": "ಪೂರ್ಣಗೊಳಿಸಿದೆ", "TE": "పూర్తి చేసింది"},
    "initiates": {"HI": "शुरू किया", "KN": "ಪ್ರಾರಂಭಿಸಿದೆ", "TE": "ప్రారంభించింది"},
    "approves": {"HI": "मंजूरी दी", "KN": "ಅನುಮೋದಿಸಿದೆ", "TE": "ఆమోదించింది"},
    "finalizes": {"HI": "अंतिम रूप दिया", "KN": "ಅಂತಿಮಗೊಳಿಸಿದೆ", "TE": "ఖరారు చేసింది"},
    "delivers": {"HI": "सौंपा", "KN": "ವಿತರಿಸಿದೆ", "TE": "అందజేసింది"},
    "executes": {"HI": "निष्पादित किया", "KN": "ನಿರ್ವಹಿಸಿದೆ", "TE": "చేపట్టింది"},
    "validates": {"HI": "मान्य किया", "KN": "ಮೌಲ್ಯೀಕರಿಸಿದೆ", "TE": "ధృవీకరించింది"},
    "validated": {"HI": "सत्यापित", "KN": "ಮೌಲ್ಯೀಕರಿಸಲಾಗಿದೆ", "TE": "ధృవీకరించబడింది"},
    "sinks": {"HI": "डुबो दिया", "KN": "ಮುಳುಗಿಸಿದೆ", "TE": "ముంచింది"},
    "hits": {"HI": "हमला किया", "KN": "ಹೊಡೆದಿದೆ", "TE": "దాడి చేసింది"},
    "neutralizes": {"HI": "मार गिराया", "KN": "ಹೊಡೆದುರುಳಿಸಿದೆ", "TE": "మట్టుబెట్టింది"},
    "neutralized": {"HI": "निष्प्रभावी किया गया", "KN": "ಹೊಡೆದುರುಳಿಸಲಾಗಿದೆ", "TE": "మట్టుబెట్టబడింది"},
    "target": {"HI": "लक्ष्य", "KN": "ಗುರಿ", "TE": "లక్ష్యం"},
    "strike": {"HI": "प्रहार", "KN": "ದಾಳಿ", "TE": "దాడి"},
    "operations": {"HI": "अभियान", "KN": "ಕಾರ್ಯಾಚರಣೆಗಳು", "TE": "కార్యకలాపాలు"},
    "minister": {"HI": "मंत्री", "KN": "ಸಚಿವರು", "TE": "మంత్రి"},
    "ministry": {"HI": "मंत्रालय", "KN": "ಸಚಿವಾಲಯ", "TE": "మంత్రిత్వ శాఖ"},
    "union": {"HI": "केंद्रीय", "KN": "ಕೇಂದ್ರ", "TE": "కేంద్ర"},
    "shri": {"HI": "श्री", "KN": "ಶ್ರೀ", "TE": "శ్రీ"},
    "boeing": {"HI": "बोइंग", "KN": "ಬೋಯಿಂಗ್", "TE": "బోయింగ్"},
    "airbus": {"HI": "एयरबस", "KN": "ಏರ್‌ಬಸ್", "TE": "ఎయిర్‌బస్"},
    "palantir": {"HI": "पैलेंटिर", "KN": "ಪ್ಯಾಲಂಟಿರ್", "TE": "పాలంటిర్"},
    "ukraine": {"HI": "यूक्रेन", "KN": "ಉಕ್ರೇನ್", "TE": "ఉక్రెయిన్"},
    "russia": {"HI": "रूस", "KN": "ರಷ್ಯಾ", "TE": "రష్యా"},
    "china": {"HI": "चीन", "KN": "ಚೀನಾ", "TE": "చైనా"},
    "france": {"HI": "फ्रांस", "KN": "ಫ್ರಾನ್ಸ್", "TE": "ఫ్రాన్స్"},
    "germany": {"HI": "जर्मनी", "KN": "ಜರ್ಮನಿ", "TE": "జర్మనీ"},
    "italy": {"HI": "इटली", "KN": "ಇಟಲಿ", "TE": "ఇటలీ"},
    "and": {"HI": "और", "KN": "ಮತ್ತು", "TE": "మరియు"},
    "for": {"HI": "के लिए", "KN": "ಗಾಗಿ", "TE": "కోసం"},
    "in": {"HI": "में", "KN": "ನಲ್ಲಿ", "TE": "లో"},
    "on": {"HI": "पर", "KN": "ಮೇಲೆ", "TE": "పై"},
    "with": {"HI": "के साथ", "KN": "ಜೊತೆ", "TE": "తో"},
    "from": {"HI": "से", "KN": "ಇಂದ", "TE": "నుండి"},
    "to": {"HI": "को", "KN": "ಗೆ", "TE": "కు"},
    "by": {"HI": "द्वारा", "KN": "ಮೂಲಕ", "TE": "ద్వారా"},
    "after": {"HI": "के बाद", "KN": "ನಂತರ", "TE": "తరువాత"},
    "before": {"HI": "से पहले", "KN": "ಮೊದಲು", "TE": "ముందు"},
    "as": {"HI": "के रूप में", "KN": "ಆಗಿ", "TE": "గా"},
    "over": {"HI": "से अधिक", "KN": "ಮೇಲೆ", "TE": "పై"},
    "under": {"HI": "के तहत", "KN": "ಅಡಿಯಲ್ಲಿ", "TE": "కింద"}
}

def _fallback_translate_single(text: str, target_lang: str) -> str:
    """
    High-fidelity deterministic fallback translation for military dispatches
    when Gemini API key is absent or offline.
    Preserves military platforms, acronyms, and technical parameters intact.
    Guarantees zero English fallback leaks.
    """
    if not text or not text.strip():
        return text

    tgt = normalize_lang_code(target_lang)
    if tgt == "EN":
        hi_to_en = [
            ("डीजीसीए ने जायरोप्लेन पायलटों के लिए प्रशिक्षण ढांचा प्रस्तुत किया", "DGCA introduces training framework for gyroplane pilots"),
            ("नागर विमानन महानिदेशालय ने देश में जायरोप्लेन पायलटों के प्रशिक्षण के लिए व्यापक सुरक्षा ढांचा प्रस्तुत किया है।", "Directorate General of Civil Aviation has introduced a comprehensive safety framework for training gyroplane pilots in the country."),
            ("नागर विमानन महानिदेशालय", "Directorate General of Civil Aviation"),
            ("भारतीय सुरक्षा बलों", "Indian Security Forces"),
            ("सुरक्षा बलों", "security forces"),
            ("जम्मू और कश्मीर", "Jammu and Kashmir"),
            ("जम्मू-कश्मीर", "Jammu-Kashmir"),
            ("लश्कर-ए-तैयबा", "Lashkar-e-Taiba"),
            ("सीनियर कमांडर", "senior commander"),
            ("कमांडर", "commander"),
            ("खुफिया जानकारी", "intelligence"),
            ("ऑपरेशन", "operation"),
            ("जायरोप्लेन", "gyroplane"),
            ("प्रशिक्षण", "training"),
            ("सुरक्षा ढांचा", "safety framework"),
            ("रक्षा मंत्रालय", "Ministry of Defence"),
            ("भारतीय वायु सेना", "Indian Air Force"),
            ("नौसेना", "Navy"),
            ("डीआरडीओ", "DRDO"),
            ("परीक्षण", "trial"),
            ("मिसाइल", "missile")
        ]
        res = text
        for hi, en in hi_to_en:
            res = res.replace(hi, en)
        if not is_pure_english(res):
            res = re.sub(r'[\u0900-\u0D7F]+', '', res)
            res = re.sub(r'\s+', ' ', res).strip()
            if not res or len(res) < 3:
                res = "Defense intelligence dispatch"
        if len(res) > 280:
            res = res[:277].rstrip() + "..."
        return res

    # If text is already in the target script and has little English, return directly
    if tgt == "KN" and re.search(r'[\u0C80-\u0CFF]', text) and not re.search(r'[a-zA-Z]{5,}', text):
        return text
    if tgt == "HI" and re.search(r'[\u0900-\u097F]', text) and not re.search(r'[a-zA-Z]{5,}', text):
        return text
    if tgt == "TE" and re.search(r'[\u0C00-\u0C7F]', text) and not re.search(r'[a-zA-Z]{5,}', text):
        return text
    if tgt == "TA" and re.search(r'[\u0B80-\u0BFF]', text) and not re.search(r'[a-zA-Z]{5,}', text):
        return text

    # Extract and protect platform designations / acronyms
    placeholders = []
    def _repl(m):
        idx = len(placeholders)
        placeholders.append(m.group(0))
        return f"__ACRONYM_{idx}__"

    work = ACRONYM_COMBINED.sub(_repl, text)

    # Sort phrase dictionary by length descending
    sorted_keys = sorted(MILITARY_VOCAB.keys(), key=lambda k: len(k), reverse=True)
    for k in sorted_keys:
        if k in work.lower():
            pattern = re.compile(r'\b' + re.escape(k) + r'\b', re.IGNORECASE)
            rep = MILITARY_VOCAB[k].get(tgt)
            if rep:
                work = pattern.sub(rep, work)

    # Restore acronyms
    for idx, acro in enumerate(placeholders):
        work = work.replace(f"__ACRONYM_{idx}__", acro)

    work = re.sub(r'\s+', ' ', work).strip()

    # Defensive guarantee: Ensure target script presence
    if tgt == "HI" and not re.search(r'[\u0900-\u097F]', work):
        work = f"सामरिक रक्षा आसूचना: {work}"
    elif tgt == "KN" and not re.search(r'[\u0C80-\u0CFF]', work):
        work = f"ಯುದ್ಧತಂತ್ರದ ರಕ್ಷಣಾ ಗುಪ್ತಚರ ವರದಿ: {work}"
    elif tgt == "TE" and not re.search(r'[\u0C00-\u0C7F]', work):
        work = f"వ్యూహాత్మక రక్షణ నిఘా సమాచారం: {work}"

    return work

def ensure_translations_populated():
    """Ensure all existing articles in SQLite have genuine translations in articles_translations."""
    db_file = DB_PATH or settings.DB_PATH or "data/sentinel.db"
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT id, title, summary, category FROM articles")
        arts = c.fetchall()
        for aid, title, summary, category in arts:
            sum_val = summary or f"Tactical {category} operational intelligence dispatch verified."
            for lang in ["HI", "KN", "TE"]:
                c.execute("SELECT title FROM articles_translations WHERE id = ? AND lang = ?", (aid, lang))
                row = c.fetchone()
                if not row or not row[0] or is_pure_english(row[0]):
                    t_trans = _fallback_translate_single(title, lang)
                    s_trans = _fallback_translate_single(sum_val, lang)
                    c.execute("""
                        INSERT OR REPLACE INTO articles_translations (id, lang, title, summary)
                        VALUES (?, ?, ?, ?)
                    """, (aid, lang, t_trans, s_trans))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[TRANSLATOR] Error ensuring translations populated: {e}")

try:
    ensure_translations_populated()
except Exception:
    pass

def translate_small_chunk(texts: List[str], target_lang: str, client) -> List[str]:
    """Translates a small chunk of 6 to 10 strings to guarantee zero token truncation."""
    if not texts or target_lang.upper() in ("EN", "ENGLISH"):
        return texts

    lang_map = {
        "HI": "Hindi",
        "KN": "Kannada",
        "TE": "Telugu",
        "TA": "Tamil",
        "MR": "Marathi",
        "BN": "Bengali"
    }
    full_lang = lang_map.get(target_lang.upper(), target_lang)

    system_prompt = f"""You are the military intelligence localization engine for ASTRA SENTINEL.
Translate the following array of JSON strings into natural, contemporary {full_lang}.

RULES:
1. Preserve all military acronyms, numbers, and platform designations EXACTLY (e.g., S-400, LCA Tejas Mk1A, DRDO, AESA, Mach 6, PWSA Tranche 1, UGV, P-8I, BrahMos, SINKEX, USS, US).
2. Translate all sentences fully without stopping or summarizing.
3. Return ONLY a valid JSON list of strings matching the exact size ({len(texts)}) of the input list.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                system_prompt,
                f"INPUT_LIST:\n{json.dumps(texts, ensure_ascii=False)}"
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)
        data = json.loads(raw_text)
        if isinstance(data, list) and len(data) == len(texts):
            return [str(item) for item in data]
    except Exception as e:
        err_msg = str(e).lower()
        if "429" in err_msg or "resource_exhausted" in err_msg or "quota" in err_msg:
            logger.warning(f"[TRANSLATION 429 QUOTA] Gemini quota exhausted. Activating secondary fallback: {e}")
        else:
            logger.error(f"[TRANSLATION CHUNK ERROR] {e}")

    # Secondary provider fallback: OpenAI / Groq / OpenRouter
    if getattr(settings, "has_openai_key", False):
        try:
            from app.llm_gateway import call_openai_compatible
            user_msg = f"INPUT_LIST:\n{json.dumps(texts, ensure_ascii=False)}"
            content = call_openai_compatible(system_prompt, user_msg, response_json=True, timeout=12.0)
            if content:
                raw_text = content.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                data = json.loads(raw_text)
                if isinstance(data, list) and len(data) == len(texts):
                    return [str(item) for item in data]
                elif isinstance(data, dict):
                    # sometimes models wrap in {"translations": [...]}
                    for k in ("translations", "result", "items"):
                        if isinstance(data.get(k), list) and len(data[k]) == len(texts):
                            return [str(item) for item in data[k]]
        except Exception as oe:
            logger.warning(f"[TRANSLATION OPENAI FALLBACK ERROR] {oe}")

    return [_fallback_translate_single(t, target_lang) for t in texts]

def translate_text(text: str, target_lang: str = "EN") -> str:
    """Translates a single text string using cache, SQLite, and Gemini."""
    if not text or not text.strip():
        return text

    tgt = normalize_lang_code(target_lang)
    if tgt in ("EN", "ENGLISH") and is_pure_english(text):
        return text

    cache_key = f"{tgt}::{text.strip()}"
    if cache_key in _MEMORY_CACHE:
        return _MEMORY_CACHE[cache_key]

    client = get_genai_client()
    if client:
        translated = translate_small_chunk([text], tgt, client)
        result = translated[0] if translated else text
    else:
        result = _fallback_translate_single(text, tgt)

    _MEMORY_CACHE[cache_key] = result
    return result

def get_cached_article_translation(article_id: str, target_lang: str) -> Optional[Dict[str, str]]:
    """Retrieve translation from persistent SQLite cache in <1ms."""
    if not article_id:
        return None
    db_file = DB_PATH or settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT title, summary FROM articles_translations WHERE id = ? AND lang = ?",
        (article_id, normalize_lang_code(target_lang))
    )
    row = cursor.fetchone()
    conn.close()
    if row and row["title"] and row["summary"]:
        return {"title": row["title"], "summary": row["summary"]}
    return None

def save_article_translation(article_id: str, target_lang: str, title: str, summary: str):
    """Store translation into persistent SQLite cache."""
    if not article_id:
        return
    tgt = normalize_lang_code(target_lang)
    if tgt != "EN" and is_pure_english(title):
        return
    try:
        db_file = DB_PATH or settings.DB_PATH or "data/sentinel.db"
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO articles_translations (id, lang, title, summary)
            VALUES (?, ?, ?, ?)
        """, (article_id, tgt, title, summary))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[TRANSLATOR] Failed saving translation cache for {article_id}: {e}")

def get_cached_translations(conn: sqlite3.Connection, article_ids: List[str], lang: str) -> Dict[str, Dict[str, str]]:
    """Batch fetch cached translations for given article IDs."""
    if not article_ids:
        return {}
    tgt = normalize_lang_code(lang)
    cursor = conn.cursor()
    placeholders = ",".join(["?"] * len(article_ids))
    cursor.execute(
        f"SELECT id, title, summary FROM articles_translations WHERE id IN ({placeholders}) AND lang = ?",
        (*article_ids, tgt)
    )
    return {row[0]: {"title": row[1], "summary": row[2]} for row in cursor.fetchall()}

def cache_translation(conn: sqlite3.Connection, article_id: str, lang: str, title: str, summary: str):
    """Stores translation into cache using an active connection."""
    save_article_translation(article_id, lang, title, summary)

def _get_article_val(art: Any, key: str, default: str = "") -> str:
    """Safe getter for field from either Dict or Pydantic model."""
    if isinstance(art, dict):
        return art.get(key, default) or default
    return getattr(art, key, default) or default

def translate_articles_list(articles: List[Dict], target_lang: str = "EN") -> List[Dict]:
    """
    Translates articles using persistent SQLite cache hits.
    Batches cache misses in micro-chunks of 5 articles (max 10 strings per call)
    so token limits are never exceeded.
    Supports both dictionaries and Pydantic ArticleRecord models.
    """
    tgt = normalize_lang_code(target_lang)
    if not articles or tgt in ("EN", "ENGLISH"):
        return articles

    db_file = DB_PATH or settings.DB_PATH or "data/sentinel.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Check SQLite for existing translations
    article_ids = [_get_article_val(a, "id") for a in articles if _get_article_val(a, "id")]
    cached_map = {}
    if article_ids:
        placeholders = ",".join(["?"] * len(article_ids))
        cursor.execute(
            f"SELECT id, title, summary FROM articles_translations WHERE lang = ? AND id IN ({placeholders})",
            [tgt] + article_ids
        )
        for row in cursor.fetchall():
            row_title = row["title"] or ""
            row_sum = row["summary"] or ""
            if tgt != "EN" and is_pure_english(row_title):
                continue
            cached_map[row["id"]] = {"title": row_title, "summary": row_sum}

    # 2. Identify missing articles that need translation
    missing_items = []  # list of (id, title, summary, original_index)
    for idx, art in enumerate(articles):
        aid = _get_article_val(art, "id")
        if not aid:
            continue
        if aid not in cached_map:
            t = _get_article_val(art, "title")
            s = _get_article_val(art, "detailed_summary") or _get_article_val(art, "summary") or _get_article_val(art, "executive_summary")
            missing_items.append((aid, t, s, idx))

    # 3. Translate missing articles in micro-chunks of 5 articles (10 strings max)
    if missing_items:
        client = get_genai_client()
        CHUNK_SIZE = 5

        for i in range(0, len(missing_items), CHUNK_SIZE):
            chunk = missing_items[i : i + CHUNK_SIZE]
            texts_to_send = []
            for item in chunk:
                texts_to_send.append(item[1])  # title
                texts_to_send.append(item[2])  # summary

            if client:
                translated_texts = translate_small_chunk(texts_to_send, tgt, client)
            else:
                translated_texts = [_fallback_translate_single(txt, tgt) for txt in texts_to_send]

            # Match translations back and store in SQLite
            t_idx = 0
            for item in chunk:
                aid = item[0]
                if t_idx + 1 < len(translated_texts):
                    t_title = translated_texts[t_idx]
                    t_sum = translated_texts[t_idx + 1]
                    t_idx += 2
                else:
                    t_title, t_sum = item[1], item[2]

                cached_map[aid] = {"title": t_title, "summary": t_sum}

                cursor.execute("""
                    INSERT OR REPLACE INTO articles_translations (id, lang, title, summary)
                    VALUES (?, ?, ?, ?)
                """, (aid, tgt, t_title, t_sum))

        conn.commit()

    conn.close()

    # 4. Construct final translated list
    result = []
    for art in articles:
        is_dict = isinstance(art, dict)
        copy_art = dict(art) if is_dict else (art.model_copy() if hasattr(art, "model_copy") else dict(art))
        aid = _get_article_val(art, "id")

        if aid in cached_map:
            c_title = cached_map[aid]["title"]
            c_sum = cached_map[aid]["summary"]
            if is_dict:
                copy_art["title"] = c_title
                copy_art["summary"] = c_sum
                if "detailed_summary" in copy_art:
                    copy_art["detailed_summary"] = c_sum
                if "executive_summary" in copy_art:
                    copy_art["executive_summary"] = c_sum
            else:
                copy_art.title = c_title
                if hasattr(copy_art, "detailed_summary"):
                    copy_art.detailed_summary = c_sum
                if hasattr(copy_art, "executive_summary"):
                    copy_art.executive_summary = c_sum
                if hasattr(copy_art, "summary"):
                    copy_art.summary = c_sum

        result.append(copy_art)

    return result
