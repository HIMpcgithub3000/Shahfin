#!/usr/bin/env python3
"""Synthesizes high-fidelity, complete end-to-end sample call recordings for all 10 customer personas.

Uses Edge-TTS with distinct neural voices for Bot vs. Borrower and concatenates turns with ffmpeg.
Outputs to:
  - shaha-finlease-voicebot/audio_recordings/CALL-1001.mp3 through CALL-1010.mp3
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import edge_tts

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO_RECORDINGS_DIR = os.path.join(ROOT_DIR, "audio_recordings")
PARENT_RECORDINGS_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "audio_recordings"))
os.makedirs(AUDIO_RECORDINGS_DIR, exist_ok=True)
os.makedirs(PARENT_RECORDINGS_DIR, exist_ok=True)

# Neural Voice Profiles
VOICE_BOT_HI = "hi-IN-SwaraNeural"
VOICE_MALE_HI = "hi-IN-MadhurNeural"

VOICE_BOT_EN = "en-IN-NeerjaNeural"
VOICE_MALE_EN = "en-IN-PrabhatNeural"
VOICE_FEMALE_EN = "en-IN-NeerjaExpressiveNeural"

SAMPLE_CALL_SCRIPTS = [
    # Call 1: C001 Rahul Sharma (Right customer - Hindi)
    {
        "call_id": "CALL-1001",
        "customer_id": "C001",
        "name": "Rahul Sharma",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात राहुल शर्मा जी से हो रही है?", 0.8),
            (VOICE_MALE_HI, "हाँ, मैं राहुल शर्मा बोल रहा हूँ। बताइए, किस बारे में कॉल किया है?", 0.8),
            (VOICE_BOT_HI, "धन्यवाद राहुल शर्मा जी। सुरक्षा और पहचान सत्यापन के लिए, क्या आप कृपया अपना 4 अंकों का जन्म वर्ष बता सकते हैं?", 0.8),
            (VOICE_MALE_HI, "जी, मेरा जन्म का साल 1985 है।", 0.8),
            (VOICE_BOT_HI, "पहचान सत्यापन सफल रहा। आपके सहयोग और समय के लिए बहुत-बहुत धन्यवाद राहुल शर्मा जी। हमारी टीम आपसे जल्द संपर्क करेगी। आपका दिन शुभ हो, नमस्ते।", 0.5),
        ],
    },
    # Call 2: C002 Priya Nair (Verification Failed - English)
    {
        "call_id": "CALL-1002",
        "customer_id": "C002",
        "name": "Priya Nair",
        "turns": [
            (VOICE_BOT_EN, "Hello, I am an automated assistant calling from Shaha Finlease. This call is recorded for quality and verification purposes. Am I speaking with Priya Nair?", 0.8),
            (VOICE_FEMALE_EN, "Yes, this is Priya Nair speaking. How can I help you?", 0.8),
            (VOICE_BOT_EN, "Thank you, Priya Nair. To quickly verify your contact record and protect your security, could you please state your 4-digit year of birth?", 0.8),
            (VOICE_FEMALE_EN, "Sure, my year of birth is 1996.", 0.8),
            (VOICE_BOT_EN, "Thank you for your time. The birth year provided does not match our records, so for security reasons I cannot proceed with this call. Have a good day. Goodbye.", 0.5),
        ],
    },
    # Call 3: C003 Amit Patil (Wrong Number - Hindi)
    {
        "call_id": "CALL-1003",
        "customer_id": "C003",
        "name": "Amit Patil",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात अमित पाटिल जी से हो रही है?", 0.8),
            (VOICE_MALE_HI, "अरे नहीं भाई, गलत नंबर लगाया है आपने। मैं अमित पाटिल नाम के किसी व्यक्ति को नहीं जानता।", 0.8),
            (VOICE_BOT_HI, "असुविधा के लिए हमें बेहद खेद है। हम इस नंबर को तुरंत गलत नंबर के रूप में अपडेट कर रहे हैं ताकि आपको दोबारा कॉल न आए। अपना समय देने के लिए धन्यवाद, नमस्ते।", 0.5),
        ],
    },
    # Call 4: C004 Sneha Kulkarni (Callback Requested - Hindi)
    {
        "call_id": "CALL-1004",
        "customer_id": "C004",
        "name": "Sneha Kulkarni",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात स्नेहा कुलकर्णी जी से हो रही है?", 0.8),
            (VOICE_MALE_HI, "स्नेहा अभी घर पर नहीं हैं, बाहर गई हुई हैं। मैं उनका भाई बोल रहा हूँ। क्या काम था?", 0.8),
            (VOICE_BOT_HI, "जानकारी के लिए धन्यवाद। क्या आप बता सकते हैं कि स्नेहा कुलकर्णी जी से बात करने के लिए किस समय दोबारा कॉल करना उचित रहेगा?", 0.8),
            (VOICE_MALE_HI, "आप उन्हें आज शाम को 6:30 बजे कॉल कर लीजिए, तब वो घर पर मिल जाएंगी।", 0.8),
            (VOICE_BOT_HI, "बहुत-बहुत धन्यवाद। हमने आज शाम 6:30 बजे का कॉलबैक समय दर्ज कर लिया है। आपका दिन शुभ हो, नमस्ते।", 0.5),
        ],
    },
    # Call 5: C005 Vikram Singh (Silence / 15s Timeout - English)
    {
        "call_id": "CALL-1005",
        "customer_id": "C005",
        "name": "Vikram Singh",
        "turns": [
            (VOICE_BOT_EN, "Hello, I am an automated assistant calling from Shaha Finlease. This call is recorded for quality and verification purposes. Am I speaking with Vikram Singh?", 3.0),
            (VOICE_BOT_EN, "Please confirm if you can hear me.", 3.0),
            (VOICE_BOT_EN, "We could not hear your response. The call will be ended now. Goodbye.", 0.5),
        ],
    },
    # Call 6: C006 Anjali Deshmukh (Right Customer - Hindi)
    {
        "call_id": "CALL-1006",
        "customer_id": "C006",
        "name": "Anjali Deshmukh",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात अंजलि देशमुख जी से हो रही है?", 0.8),
            (VOICE_BOT_HI, "जी हाँ, मैं अंजलि देशमुख बोल रही हूँ। कहिए क्या बात है?", 0.8),
            (VOICE_BOT_HI, "धन्यवाद अंजलि देशमुख जी। सुरक्षा और पहचान सत्यापन के लिए, क्या आप कृपया अपना 4 अंकों का जन्म वर्ष बता सकती हैं?", 0.8),
            (VOICE_BOT_HI, "हाँ, मेरा जन्म का साल 1995 है।", 0.8),
            (VOICE_BOT_HI, "सत्यापन पूर्ण और सफल रहा। आपके सहयोग के लिए बहुत-बहुत धन्यवाद अंजलि देशमुख जी। हमारी टीम आपसे शीघ्र ही संपर्क करेगी। नमस्ते।", 0.5),
        ],
    },
    # Call 7: C007 Rohan Mehta (Verification Failed - English)
    {
        "call_id": "CALL-1007",
        "customer_id": "C007",
        "name": "Rohan Mehta",
        "turns": [
            (VOICE_BOT_EN, "Hello, I am an automated assistant calling from Shaha Finlease. This call is recorded for quality and verification purposes. Am I speaking with Rohan Mehta?", 0.8),
            (VOICE_MALE_EN, "Yes, I am Rohan Mehta. Who is calling?", 0.8),
            (VOICE_BOT_EN, "Thank you, Rohan Mehta. To protect your privacy and complete verification, could you please provide your 4-digit birth year?", 0.8),
            (VOICE_MALE_EN, "Sure, I was born in 1990.", 0.8),
            (VOICE_BOT_EN, "Thank you for your time. The birth year provided does not match our records, so I cannot disclose any details or proceed further. Have a good day. Goodbye.", 0.5),
        ],
    },
    # Call 8: C008 Kavita Joshi (Wrong Number - Hindi)
    {
        "call_id": "CALL-1008",
        "customer_id": "C008",
        "name": "Kavita Joshi",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात कविता जोशी जी से हो रही है?", 0.8),
            (VOICE_BOT_HI, "नहीं, यह उनका नंबर नहीं है। आपने गलत नंबर डायल किया है, यहाँ कोई कविता जोशी नहीं रहती।", 0.8),
            (VOICE_BOT_HI, "असुविधा के लिए हम क्षमा चाहते हैं। हम इस नंबर को तुरंत गलत नंबर के रूप में दर्ज कर रहे हैं। आपके समय के लिए धन्यवाद, नमस्ते।", 0.5),
        ],
    },
    # Call 9: C009 Suresh Yadav (Third Party Answered - Hindi)
    {
        "call_id": "CALL-1009",
        "customer_id": "C009",
        "name": "Suresh Yadav",
        "turns": [
            (VOICE_BOT_HI, "नमस्ते, मैं Shaha Finlease से एक ऑटोमेटेड असिस्टेंट बोल रही हूँ। यह कॉल सेवा गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात सुरेश यादव जी से हो रही है?", 0.8),
            (VOICE_MALE_HI, "सुरेश जी अभी घर पर नहीं हैं, बाहर गए हुए हैं। क्या बात थी?", 0.8),
            (VOICE_BOT_HI, "जानकारी के लिए धन्यवाद। क्या आप बता सकते हैं कि सुरेश यादव जी से बात करने के लिए हम किस समय दोबारा कॉल कर सकते हैं?", 0.8),
            (VOICE_MALE_HI, "मुझे नहीं मालूम वो कब तक लौटेंगे, कोई पक्का समय नहीं बता सकता।", 0.8),
            (VOICE_BOT_HI, "कोई बात नहीं, जानकारी देने के लिए धन्यवाद। हम बाद में पुनः प्रयास करेंगे। आपका दिन शुभ हो, नमस्ते।", 0.5),
        ],
    },
    # Call 10: C010 Neha Gupta (Dropped Midway - English)
    {
        "call_id": "CALL-1010",
        "customer_id": "C010",
        "name": "Neha Gupta",
        "turns": [
            (VOICE_BOT_EN, "Hello, I am an automated assistant calling from Shaha Finlease. This call is recorded for quality and verification purposes. Am I speaking with Neha Gupta?", 1.5),
            (VOICE_BOT_EN, "Call disconnected by recipient. Ending session. Goodbye.", 0.5),
        ],
    },
]


def make_silence_mp3(duration: float, out_path: str):
    """Generates an empty audio segment of given duration in seconds."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration), "-q:a", "9", "-acodec", "libmp3lame", out_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


async def synthesize_turn_audio(text: str, voice: str, out_path: str, max_retries: int = 4):
    """Synthesizes text using Edge-TTS into an MP3 file with automatic retry."""
    for attempt in range(max_retries):
        try:
            comm = edge_tts.Communicate(text, voice)
            await comm.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(1.5 * (attempt + 1))


def concat_turn_files(file_list: list, out_path: str):
    """Concatenates list of MP3 files into a single seamless audio file."""
    temp_list = out_path + ".txt"
    with open(temp_list, "w") as f:
        for fp in file_list:
            f.write(f"file '{fp}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", temp_list,
        "-c", "copy", out_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if os.path.exists(temp_list):
        os.remove(temp_list)


async def build_sample_call_audio(spec: dict):
    call_id = spec["call_id"]
    cust_name = spec["name"]
    out_path = os.path.join(AUDIO_RECORDINGS_DIR, f"{call_id}.mp3")

    # If already synthesized and healthy size, skip
    if os.path.exists(out_path) and os.path.getsize(out_path) > 100000 and call_id not in ("CALL-1009", "CALL-1010"):
        print(f"  ✓ {call_id}.mp3 already drafted ({os.path.getsize(out_path)} bytes), skipping...")
        return

    print(f"[*] Synthesizing full audio for {call_id}: {cust_name}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        turn_files = []
        for idx, (voice, text, pause_after) in enumerate(spec["turns"]):
            turn_file = os.path.join(tmpdir, f"turn_{idx}.mp3")
            await synthesize_turn_audio(text, voice, turn_file)
            turn_files.append(turn_file)

            if pause_after > 0:
                silence_file = os.path.join(tmpdir, f"pause_{idx}.mp3")
                make_silence_mp3(pause_after, silence_file)
                turn_files.append(silence_file)

        out_path = os.path.join(AUDIO_RECORDINGS_DIR, f"{call_id}.mp3")
        concat_turn_files(turn_files, out_path)

        dur_cmd = ["ffprobe", "-i", out_path, "-show_entries", "format=duration", "-v", "quiet", "-of", "csv=p=0"]
        dur = subprocess.check_output(dur_cmd).decode().strip()
        print(f"  ✓ {call_id}.mp3 created ({float(dur):.1f}s, {os.path.getsize(out_path)} bytes)")


async def main():
    print("=" * 70)
    print("REDRAFTING ALL 10 SAMPLE CALL RECORDINGS WITH COMPLETE DIALOGUES")
    print("=" * 70)
    for spec in SAMPLE_CALL_SCRIPTS:
        await build_sample_call_audio(spec)
    print("=" * 70)
    print("✓ Successfully generated all 10 sample call audios!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
