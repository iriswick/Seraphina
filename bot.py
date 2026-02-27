import discord
from discord.ext import commands
import os
import requests
import asyncio
import time
import wave
import threading
import base64
import edge_tts
import chess
import chess.engine
import random
import urllib.parse
import speech_recognition as sr
from dotenv import load_dotenv
from discord.ext import voice_recv # <--- NEW IMPORT
import boto3 # <--- NEW IMPORT (We will use this for AWS soon)

# Load the environment variables
load_dotenv()
# --- BOT MEMORY ---
conversation_history = {}
active_chess_games = {}
active_game_states = {} # <--- Tracks the status of ANY game you play!

def get_seraphina_prompt(uid):
    """Dynamically builds Seraphina's personality AND injects what she sees in the text chat!"""
    base_prompt = "You are Seraphina, a friendly, witty Discord companion. Keep your responses short, conversational, and natural. NEVER use emojis, emoticons, or special characters in your responses."
    
    # If the user is playing a game in text chat, tell her about it!
    if uid in active_game_states:
        current_game = active_game_states[uid]
        base_prompt += f" IMPORTANT CONTEXT: You are currently playing {current_game['name']} with the user in the text channel. The current status is: {current_game['state']}."
        
    return [{"text": base_prompt}]
# --- VAD (Voice Activity Detection) Memory ---
audio_buffers = {}       # Stores the raw audio bytes while you talk
last_packet_times = {}   # Tracks the exact millisecond you last made a sound
audio_lock = threading.Lock() # Prevents threading crashes
api_key = os.getenv('NOVA_API_KEY')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}!')
    print('Ready to chat in Discord!')
    
    # Start the background silence monitor
    bot.loop.create_task(silence_monitor())

# --- STANDARD COMMANDS ---
@bot.command()
async def hello(ctx):
    await ctx.send("Hello! I'm ready to chat and play games.")

def my_audio_callback(user, data: voice_recv.VoiceData):
    """Triggers every time Discord sends a 20ms chunk of your voice."""
    if not user:
        return
        
    with audio_lock:
        # If this is the start of a new sentence, create an empty bucket
        if user.id not in audio_buffers:
            audio_buffers[user.id] = bytearray()
            print(f"🎙️ {user.name} started speaking...")
            
        # Toss the raw audio chunk into the bucket and update the clock
        audio_buffers[user.id].extend(data.pcm)
        last_packet_times[user.id] = time.time()

@bot.group(invoke_without_command=True)
async def chess_game(ctx):
    """The main command group for playing chess."""
    await ctx.send("♟️ Use `!chess_game start` to begin a game, and `!chess_game move e2e4` to play!")

@bot.command()
async def flip(ctx, guess: str):
    """A simple coin flip game to test her generic memory!"""
    result = random.choice(["heads", "tails"])
    
    if guess.lower() == result:
        outcome = "The user guessed correctly and won!"
        await ctx.send(f"It's {result}! You win! 🎉")
    else:
        outcome = "The user guessed wrong and lost."
        await ctx.send(f"It's {result}! You lose. 😔")
        
    # Inject this new game into her brain!
    active_game_states[ctx.author.id] = {
        "name": "Coin Toss",
        "state": f"You just flipped a coin. {outcome}"
    }

@chess_game.command()
async def start(ctx):
    """Starts a new game of chess."""
    if ctx.author.id in active_chess_games:
        await ctx.send("We already have a game going! Use `!chess_game stop` to reset it.")
        return
    
    # 1. Create a brand new digital chess board
    board = chess.Board()
    active_chess_games[ctx.author.id] = board
    
    # 2. Convert the board to FEN and generate the image URL!
    encoded_fen = urllib.parse.quote(board.fen())
    image_url = f"https://fen2image.chessvision.ai/{encoded_fen}"
    
    await ctx.send(f"Alright, let's play! You are White. Make your move using `!chess_game move e2e4`.\n{image_url}")

@chess_game.command()
async def move(ctx, user_move: str):
    """Allows the player to make a move using UCI format (e.g., e2e4)."""
    if ctx.author.id not in active_chess_games:
        await ctx.send("We aren't playing a game right now! Use `!chess_game start`.")
        return

    board = active_chess_games[ctx.author.id]

    # --- 1. THE PLAYER'S TURN ---
    try:
        # Convert the text (e2e4) into a digital move
        move = chess.Move.from_uci(user_move.lower())
        if move in board.legal_moves:
            board.push(move)
        else:
            await ctx.send("That is an illegal move! Try again.")
            return
    except ValueError:
        await ctx.send("I didn't understand that. Use standard UCI format like `e2e4` or `g1f3`.")
        return

    # Check if the player won
    if board.is_checkmate():
        await ctx.send(f"Checkmate! You beat me, {ctx.author.name}! 🎉")
        del active_chess_games[ctx.author.id]
        if ctx.author.id in active_game_states:          
            del active_game_states[ctx.author.id]         
        return

    # --- 2. SERAPHINA'S TURN (STOCKFISH ENGINE) ---
    try:
        # Boot up the Stockfish brain (make sure stockfish.exe is in the same folder!)
        engine = chess.engine.SimpleEngine.popen_uci("stockfish.exe")
        
        # Ask Stockfish to think for 0.1 seconds (which is plenty for a fast, smart move)
        # If you want her to be harder to beat, increase the time to 0.5 or 1.0!
        result = engine.play(board, chess.engine.Limit(time=0.1))
        bot_move = result.move
        
        # Play the move and turn the engine off
        board.push(bot_move)
        engine.quit()
        
    except Exception as e:
        await ctx.send("Oops! I couldn't connect to my Stockfish brain. Make sure `stockfish.exe` is in my folder!")
        print(f"STOCKFISH ERROR: {e}")
        return

    # Check if Seraphina won
    if board.is_checkmate():
        board_text = f"```text\n{board}\n```"
        await ctx.send(f"Checkmate! I win! Better luck next time. 😉\n{board_text}")
        del active_chess_games[ctx.author.id]
        if ctx.author.id in active_game_states:
            del active_game_states[ctx.author.id]
        return

   # --- 3. SHOW THE UPDATED BOARD ---
    encoded_fen = urllib.parse.quote(board.fen())
    image_url = f"https://fen2image.chessvision.ai/{encoded_fen}"
    
    await ctx.send(f"I played **{bot_move}**. Your turn!\n{image_url}")
    # Tell Seraphina's brain what just happened in the text chat!
    active_game_states[ctx.author.id] = {
        "name": "Chess",
        "state": f"The user played {user_move}. You (Seraphina) responded by playing {bot_move}. The board FEN is {board.fen()}."
    }

@chess_game.command()
async def stop(ctx):
    """Ends the current game."""
    if ctx.author.id in active_chess_games:
        del active_chess_games[ctx.author.id]
        await ctx.send("Game stopped. I'll put the pieces away!")
    if ctx.author.id in active_game_states:           
            del active_game_states[ctx.author.id]
    else:
        await ctx.send("We aren't playing right now!")

async def silence_monitor():
    """Continuously checks if anyone has stopped talking for 1.5 seconds."""
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        now = time.time()
        users_to_process = []
        
        with audio_lock:
            for uid, last_time in list(last_packet_times.items()):
                # If 1.5 seconds of silence has passed
                if now - last_time > 1.5:
                    audio_data = bytes(audio_buffers[uid])
                    if len(audio_data) > 0:
                        users_to_process.append((uid, audio_data))
                        
                    # Clear their buckets for the next sentence
                    del audio_buffers[uid]
                    del last_packet_times[uid]
                    
        # Process the completed sentences
        for uid, audio_data in users_to_process:
            bot.loop.create_task(process_completed_sentence(uid, audio_data))
            
        await asyncio.sleep(0.1) # Check every 100ms

async def process_completed_sentence(uid, pcm_data):
    """Takes the completed audio bucket and saves it as a .wav file in a separate folder."""
    user = bot.get_user(uid)
    user_name = user.name if user else f"User_{uid}"
    
    print(f"✅ Silence detected! Captured a full phrase from {user_name}.")
    
    # 1. Create a 'recordings' folder if it doesn't already exist
    os.makedirs("recordings", exist_ok=True)
    
    # 2. Update the filename to save inside the new folder
    filename = f"recordings/user_{uid}_input.wav"
    
    try:
        with wave.open(filename, 'wb') as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(pcm_data)
            
        print(f"💾 Saved {len(pcm_data)} bytes to {filename}. Ready for Amazon Nova!")
        
        # UPDATE THIS LINE:
        bot.loop.create_task(send_to_nova_and_speak(uid, filename))
        
    except Exception as e:
        print(f"❌ Error saving audio file: {e}")
    
async def send_to_nova_and_speak(uid, input_filename):
    """Transcribes audio, uses shared memory, sends to Nova, and speaks the reply."""
    
    # --- 1. SPEECH TO TEXT ---
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(input_filename) as source:
            audio_data = recognizer.record(source)
            
        print("🦻 Transcribing your voice...")
        user_text = recognizer.recognize_google(audio_data)
        print(f"👤 You said: '{user_text}'")
        
    except sr.UnknownValueError:
        print("❌ Could not understand the audio. It might have been too quiet.")
        return
    except Exception as e:
        print(f"❌ Transcription error: {e}")
        return

    # --- 2. MEMORY MANAGEMENT ---
    if uid not in conversation_history:
        conversation_history[uid] = []
        
    conversation_history[uid].append({
        "role": "user",
        "content": [{"text": user_text}]
    })
    
    # Keep the memory from getting too huge (keeps the last 10 messages)
    if len(conversation_history[uid]) > 10:
        conversation_history[uid] = conversation_history[uid][-10:]
        
        # 🚨 THE FIX: Ensure the trimmed list ALWAYS starts with a user message
        if conversation_history[uid][0]["role"] == "assistant":
            conversation_history[uid].pop(0) # Remove the dangling assistant message
    # --- 3. AMAZON NOVA LITE (LLM) ---
    print("🧠 Sending conversation history to Amazon Nova Lite...")
    url = "https://bedrock-runtime.us-east-1.amazonaws.com/model/amazon.nova-lite-v1:0/converse"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        # 🚨 THE MAGIC FIX: She now "sees" the game states every time she speaks!
        "system": get_seraphina_prompt(uid), 
        "messages": conversation_history[uid]
    }

    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload)
        
        if response.status_code == 200:
            response_data = response.json()
            reply_text = response_data['output']['message']['content'][0]['text']
            print(f"💬 Seraphina says: {reply_text}")
            
            # Save Seraphina's response back into the memory bank!
            conversation_history[uid].append({
                "role": "assistant",
                "content": [{"text": reply_text}]
            })
            
            # --- 4. TEXT TO SPEECH ---
            output_filename = f"recordings/bot_response_{uid}.mp3"
            
            communicate = edge_tts.Communicate(reply_text, "en-GB-SoniaNeural")
            await communicate.save(output_filename)
            print("🔊 Audio generated! Playing in Discord now...")
            
            for vc in bot.voice_clients:
                if vc.is_connected() and not vc.is_playing():
                    vc.play(discord.FFmpegPCMAudio(output_filename))
                    break
        else:
            # If the API fails, remove your last message so memory isn't corrupted
            conversation_history[uid].pop()
            print(f"API ERROR {response.status_code}: {response.text}")
            
    except Exception as e:
        # Same here, pop the message if the code crashes
        conversation_history[uid].pop()
        print(f"CODE ERROR: {e}")

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        
        # 1. Connect using the special voice_recv client so the bot isn't deaf
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        await ctx.send(f"Joined {channel.name} and I am listening!")
        
        # 2. Tell the bot to start dumping audio into our basic sink
        vc.listen(voice_recv.BasicSink(my_audio_callback))
        
    else:
        await ctx.send("You need to be in a voice channel first!")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        # Stop listening before disconnecting
        if hasattr(ctx.voice_client, 'stop_listening'):
            ctx.voice_client.stop_listening()
        await ctx.voice_client.disconnect()
        await ctx.send("Left the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")

# --- AMAZON NOVA AI BRAIN WITH MEMORY ---
def get_nova_response(user_id, user_text):
    """Sends the conversation history to Amazon Nova."""
    url = "https://bedrock-runtime.us-east-1.amazonaws.com/model/amazon.nova-lite-v1:0/converse"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # 1. If this is a new user, start a fresh memory list for them
    if user_id not in conversation_history:
        conversation_history[user_id] = []
        
    # --- 2. MEMORY MANAGEMENT ---
    if user_id not in conversation_history:
        conversation_history[user_id] = []
        
    conversation_history[user_id].append({
        "role": "user",
        "content": [{"text": user_text}]
    })
    
    # Keep the memory from getting too huge (keeps the last 10 messages)
    if len(conversation_history[user_id]) > 10:
        conversation_history[user_id] = conversation_history[user_id][-10:]
        
        # 🚨 THE FIX: Ensure the trimmed list ALWAYS starts with a user message
        if conversation_history[user_id][0]["role"] == "assistant":
            conversation_history[user_id].pop(0) # Remove the dangling assistant message
    # 3. Send the ENTIRE memory list to Amazon instead of just the new text
    payload = {
        # 🚨 THE FIX: Use the dynamic prompt here so she sees the game in text chat too!
        "system": get_seraphina_prompt(user_id), 
        "messages": conversation_history[user_id], 
        "inferenceConfig": {
            "maxTokens": 512,
            "temperature": 0.7
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            response_data = response.json()
            reply_text = response_data['output']['message']['content'][0]['text']
            
            # 4. Add the bot's own reply to the memory so it remembers what it just said!
            conversation_history[user_id].append({
                "role": "assistant",
                "content": [{"text": reply_text}]
            })
            
            return reply_text
        else:
            # If the API fails, remove the user's last message so the memory doesn't get corrupted
            conversation_history[user_id].pop()
            print(f"API ERROR {response.status_code}: {response.text}")
            return "Oops! I'm having trouble thinking right now."
            
    except Exception as e:
        conversation_history[user_id].pop()
        print(f"CODE ERROR: {e}")
        return "Oops! My brain crashed."

@bot.event
async def on_message(message):
    # Ignore messages sent by the bot itself so it doesn't talk to itself
    if message.author == bot.user:
        return

    # Let the bot handle ! commands normally
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    # Treat everything else as a conversation!
    async with message.channel.typing():
        # Pass the user's unique Discord ID to the function
        reply = get_nova_response(message.author.id, message.content)
        await message.reply(reply)

# Run the bot securely
discord_token = os.getenv('DISCORD_TOKEN')
if discord_token:
    bot.run(discord_token)
else:
    print("Error: DISCORD_TOKEN not found in .env file.")
