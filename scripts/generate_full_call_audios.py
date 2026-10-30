"""Script to synthesize complete end-to-end call recordings using edge-tts and ffmpeg."""
import asyncio
import os
import subprocess
import edge_tts

AUDIO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "audio_demos"))
os.makedirs(AUDIO_DIR, exist_ok=True)

VOICE_BOT_HI = "hi-IN-SwaraNeural"
VOICE_USER_HI = "hi-IN-MadhurNeural"
VOICE_BOT_EN = "en-IN-NeerjaNeural"
VOICE_USER_EN = "en-IN-PrabhatNeural"

async def gen_turn(text: str, voice: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)

def make_silence(duration: float, out_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
        "-t", str(duration), "-q:a", "9", "-acodec", "libmp3lame", out_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def concat_mp3s(file_list: list, out_path: str):
    list_file = os.path.join(AUDIO_DIR, "concat_list.txt")
    with open(list_file, "w") as f:
        for fpath in file_list:
            f.write(f"file '{fpath}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", out_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if os.path.exists(list_file):
        os.remove(list_file)

async def build_right_customer_call():
    print("Synthesizing Full Call 1: Right Customer (Rahul Sharma in Hindi)...")
    silence_file = os.path.join(AUDIO_DIR, "silence_07.mp3")
    make_silence(0.7, silence_file)

    turns = [
        ("t1_bot.mp3", VOICE_BOT_HI, "नमस्ते, मैं शाहा फिनलीज़ से एक ऑटोमेटेड असिस्टेंट बोल रहा हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात राहुल शर्मा जी से हो रही है?"),
        ("t1_user.mp3", VOICE_USER_HI, "हाँ, मैं राहुल शर्मा बोल रहा हूँ। आप कौन?"),
        ("t2_bot.mp3", VOICE_BOT_HI, "धन्यवाद राहुल शर्मा जी। सुरक्षा सत्यापन के लिए, क्या आप कृपया अपना 4 अंकों का जन्म वर्ष बता सकते हैं?"),
        ("t2_user.mp3", VOICE_USER_HI, "जी, मेरा जन्म वर्ष 1985 है।"),
        ("t3_bot.mp3", VOICE_BOT_HI, "सत्यापन सफल रहा। आपके सहयोग के लिए बहुत-बहुत धन्यवाद राहुल शर्मा जी। हमारी टीम जल्द ही आपसे संपर्क करेगी। आपका दिन शुभ हो, नमस्ते।"),
    ]

    files_to_concat = []
    for fname, voice, text in turns:
        fpath = os.path.join(AUDIO_DIR, fname)
        await gen_turn(text, voice, fpath)
        files_to_concat.append(fpath)
        files_to_concat.append(silence_file)

    # remove trailing silence
    files_to_concat.pop()

    out_file = os.path.join(AUDIO_DIR, "full_call_1_right_customer.mp3")
    concat_mp3s(files_to_concat, out_file)
    print(f"Created: {out_file} ({os.path.getsize(out_file)} bytes)")
    return out_file

async def build_wrong_number_call():
    print("Synthesizing Full Call 2: Wrong Number (Amit Patil in Hindi)...")
    silence_file = os.path.join(AUDIO_DIR, "silence_07.mp3")

    turns = [
        ("wn_t1_bot.mp3", VOICE_BOT_HI, "नमस्ते, मैं शाहा फिनलीज़ से एक ऑटोमेटेड असिस्टेंट बोल रहा हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात अमित पाटिल जी से हो रही है?"),
        ("wn_t1_user.mp3", VOICE_USER_HI, "नहीं भाई, गलत नंबर है। मैं किसी अमित पाटिल को नहीं जानता।"),
        ("wn_t2_bot.mp3", VOICE_BOT_HI, "माफ़ कीजिएगा, गलत नंबर डायल हो गया। आपका समय देने के लिए धन्यवाद, नमस्ते।"),
    ]

    files_to_concat = []
    for fname, voice, text in turns:
        fpath = os.path.join(AUDIO_DIR, fname)
        await gen_turn(text, voice, fpath)
        files_to_concat.append(fpath)
        files_to_concat.append(silence_file)

    files_to_concat.pop()
    out_file = os.path.join(AUDIO_DIR, "full_call_2_wrong_number.mp3")
    concat_mp3s(files_to_concat, out_file)
    print(f"Created: {out_file} ({os.path.getsize(out_file)} bytes)")
    return out_file

async def build_relative_callback_call():
    print("Synthesizing Full Call 3: Relative Callback Request (Sneha Kulkarni in Hindi)...")
    silence_file = os.path.join(AUDIO_DIR, "silence_07.mp3")

    turns = [
        ("rel_t1_bot.mp3", VOICE_BOT_HI, "नमस्ते, मैं शाहा फिनलीज़ से एक ऑटोमेटेड असिस्टेंट बोल रहा हूँ। यह कॉल गुणवत्ता और सत्यापन के लिए रिकॉर्ड की जा रही है। क्या मेरी बात स्नेहा कुलकर्णी जी से हो रही है?"),
        ("rel_t1_user.mp3", VOICE_USER_HI, "नहीं, स्नेहा अभी घर पर नहीं हैं, मैं उनका भाई बोल रहा हूँ। क्या काम था?"),
        ("rel_t2_bot.mp3", VOICE_BOT_HI, "जी कोई बात नहीं। क्या आप बता सकते हैं कि स्नेहा जी से बात करने के लिए किस समय दोबारा कॉल करना ठीक रहेगा?"),
        ("rel_t2_user.mp3", VOICE_USER_HI, "आप उन्हें शाम को 6:30 बजे कॉल कर लीजिए, तब वो मिल जाएंगी।"),
        ("rel_t3_bot.mp3", VOICE_BOT_HI, "बहुत धन्यवाद, हम शाम 6:30 बजे दोबारा संपर्क करेंगे। आपका दिन शुभ हो, नमस्ते।"),
    ]

    files_to_concat = []
    for fname, voice, text in turns:
        fpath = os.path.join(AUDIO_DIR, fname)
        await gen_turn(text, voice, fpath)
        files_to_concat.append(fpath)
        files_to_concat.append(silence_file)

    files_to_concat.pop()
    out_file = os.path.join(AUDIO_DIR, "full_call_3_relative_callback.mp3")
    concat_mp3s(files_to_concat, out_file)
    print(f"Created: {out_file} ({os.path.getsize(out_file)} bytes)")
    return out_file

async def main():
    call1 = await build_right_customer_call()
    call2 = await build_wrong_number_call()
    call3 = await build_relative_callback_call()
    print("All 3 full calls generated successfully!")
    return call1

if __name__ == "__main__":
    asyncio.run(main())

