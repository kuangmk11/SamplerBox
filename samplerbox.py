#
#  SamplerBox
#
#  author:    Joseph Ernest (twitter: @JosephErnest, mail: contact@samplerbox.org)
#  url:       http://www.samplerbox.org/
#  license:   Creative Commons ShareAlike 3.0 (http://creativecommons.org/licenses/by-sa/3.0/)
#
#  samplerbox.py: Main file (now requiring at least Python 3.7)
#

#########################################
# IMPORT
# MODULES
#########################################

from config import *
import wave
import time
import numpy
import os
import re
import sounddevice
import threading
from chunk import Chunk
import struct
import rtmidi_python as rtmidi
import samplerbox_audio

oneshot_mode = False

#########################################
# SLIGHT MODIFICATION OF PYTHON'S WAVE MODULE
# TO READ CUE MARKERS & LOOP MARKERS
#########################################

class waveread(wave.Wave_read):
    def initfp(self, file):
        self._convert = None
        self._soundpos = 0
        self._cue = []
        self._loops = []
        self._ieee = False
        self._file = Chunk(file, bigendian=0)
        if self._file.getname() != b'RIFF':
            raise IOError('file does not start with RIFF id')
        if self._file.read(4) != b'WAVE':
            raise IOError('not a WAVE file')
        self._fmt_chunk_read = 0
        self._data_chunk = None
        while 1:
            self._data_seek_needed = 1
            try:
                chunk = Chunk(self._file, bigendian=0)
            except EOFError:
                break
            chunkname = chunk.getname()
            if chunkname == b'fmt ':
                self._read_fmt_chunk(chunk)
                self._fmt_chunk_read = 1
            elif chunkname == b'data':
                if not self._fmt_chunk_read:
                    raise IOError('data chunk before fmt chunk')
                self._data_chunk = chunk
                self._nframes = chunk.chunksize // self._framesize
                self._data_seek_needed = 0
            elif chunkname == b'cue ':
                numcue = struct.unpack('<i', chunk.read(4))[0]
                for i in range(numcue):
                    id, position, datachunkid, chunkstart, blockstart, sampleoffset = struct.unpack('<iiiiii', chunk.read(24))
                    self._cue.append(sampleoffset)
            elif chunkname == b'smpl':
                manuf, prod, sampleperiod, midiunitynote, midipitchfraction, smptefmt, smpteoffs, numsampleloops, samplerdata = struct.unpack(
                    '<iiiiiiiii', chunk.read(36))
                for i in range(numsampleloops):
                    cuepointid, type, start, end, fraction, playcount = struct.unpack('<iiiiii', chunk.read(24))
                    self._loops.append([start, end])
            chunk.skip()
        if not self._fmt_chunk_read or not self._data_chunk:
            raise IOError('fmt chunk and/or data chunk missing')

    def getmarkers(self):
        return self._cue

    def getloops(self):
        return self._loops

#########################################
# MIXER CLASSES
#
#########################################

class PlayingSound:
    def __init__(self, sound, note):
        self.sound = sound
        self.pos = 0
        self.fadeoutpos = 0
        self.isfadeout = False
        self.note = note

    def fadeout(self, i):
        self.isfadeout = True

    def stop(self):
        try:
            playingsounds.remove(self)
        except:
            pass

class Sound:
    def __init__(self, filename, midinote, velocity):
        wf = waveread(filename)
        self.fname = filename
        self.midinote = midinote
        self.velocity = velocity
        if wf.getloops():
            self.loop = wf.getloops()[0][0]
            self.nframes = wf.getloops()[0][1] + 2
        else:
            self.loop = -1
            self.nframes = wf.getnframes()
        self.data = self.frames2array(wf.readframes(self.nframes), wf.getsampwidth(), wf.getnchannels())
        wf.close()

    def play(self, note):
        snd = PlayingSound(self, note)
        playingsounds.append(snd)
        return snd

    def frames2array(self, data, sampwidth, numchan):
        if sampwidth == 2:
            npdata = numpy.frombuffer(data, dtype=numpy.int16)
        elif sampwidth == 3:
            npdata = samplerbox_audio.binary24_to_int16(data, len(data)//3)
        if numchan == 1:
            npdata = numpy.repeat(npdata, 2)
        return npdata

FADEOUTLENGTH = 30000
FADEOUT = numpy.linspace(1., 0., FADEOUTLENGTH)            # by default, float64
FADEOUT = numpy.power(FADEOUT, 6)
FADEOUT = numpy.append(FADEOUT, numpy.zeros(FADEOUTLENGTH, numpy.float32)).astype(numpy.float32)
SPEED = numpy.power(2, numpy.arange(0.0, 84.0)/12).astype(numpy.float32)

samples = {}
playingnotes = {}
sustainplayingnotes = []
sustain = False
playingsounds = []
globalvolume = 10 ** (-12.0/20)  # -12dB default global volume
globaltranspose = 0

#########################################
# AUDIO AND MIDI CALLBACKS
#
#########################################

def AudioCallback(outdata, frame_count, time_info, status):
    global playingsounds
    rmlist = []
    playingsounds = playingsounds[-MAX_POLYPHONY:]
    b = samplerbox_audio.mixaudiobuffers(playingsounds, rmlist, frame_count, FADEOUT, FADEOUTLENGTH, SPEED)
    for e in rmlist:
        try:
            playingsounds.remove(e)
        except:
            pass
    b *= globalvolume
    outdata[:] = b.reshape(outdata.shape)

def MidiCallback(message, time_stamp):
    print("MIDI:", message)
    global playingnotes, sustain, sustainplayingnotes, oneshot_mode
    global preset
    messagetype = message[0] >> 4
    messagechannel = (message[0] & 15) + 1
    note = message[1] if len(message) > 1 else None
    midinote = note
    velocity = message[2] if len(message) > 2 else None
    if messagetype == 9 and velocity == 0:
        messagetype = 8
    if messagetype == 9:    # Note on
        midinote += globaltranspose
        try:
            playingnotes.setdefault(midinote, []).append(samples[midinote, velocity].play(midinote))
        except:
            pass
    elif messagetype == 8:  # Note off
        if not oneshot_mode:  # ONLY stop the note if we are NOT in oneshot mode
            midinote += globaltranspose
            if midinote in playingnotes:
                for n in playingnotes[midinote]:
                    if sustain:
                        sustainplayingnotes.append(n)
                    else:
                        n.fadeout(50)
                playingnotes[midinote] = []
    elif messagetype == 12:  # Program change
        print('Program change ' + str(note))
        preset = note
        LoadSamples()
    elif (messagetype == 11) and (note == 64) and (velocity < 64):  # sustain pedal off
        for n in sustainplayingnotes:
            n.fadeout(50)
        sustainplayingnotes = []
        sustain = False
    elif (messagetype == 11) and (note == 64) and (velocity >= 64):  # sustain pedal on
        sustain = True

#########################################
# LOAD SAMPLES
#
#########################################

LoadingThread = None
LoadingInterrupt = False

def LoadSamples():
    global LoadingThread
    global LoadingInterrupt

    if LoadingThread:
        LoadingInterrupt = True
        LoadingThread.join()
        LoadingThread = None

    LoadingInterrupt = False
    LoadingThread = threading.Thread(target=ActuallyLoad)
    LoadingThread.daemon = True
    LoadingThread.start()
# Update the end of the LoadSamples() function:
    # ... existing SamplerBox code ...
    if USE_SSD1306:
        # If your version of SamplerBox defines 'basename', use that for a cleaner name
        DisplayUpdate(f"P: {preset}", basename if 'basename' in locals() else "")


NOTES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]

def ActuallyLoad():
    global preset
    global samples
    global playingsounds
    global globalvolume, globaltranspose, oneshot_mode
    oneshot_mode = False
    playingsounds = []
    samples = {}
    globalvolume = 10 ** (-12.0/20)  # -12dB default global volume
    globaltranspose = 0
    samplesdir = SAMPLES_DIR if os.listdir(SAMPLES_DIR) else '.'      # use current folder (containing 0 Saw) if no user media containing samples has been found
    basename = next((f for f in os.listdir(samplesdir) if f.startswith("%d " % preset)), None)      # or next(glob.iglob("blah*"), None)
    if basename:
        dirname = os.path.join(samplesdir, basename)
    if not basename:
        print('Preset empty: %s' % preset)
        display("E%03d" % preset)
        return
    print('Preset loading: %s (%s)' % (preset, basename))
    if USE_SSD1306:
        DisplayUpdate(basename, "Loading...")
    display("L%03d" % preset)
    definitionfname = os.path.join(dirname, "definition.txt")
    if os.path.isfile(definitionfname):
        with open(definitionfname, 'r') as definitionfile:
            for i, pattern in enumerate(definitionfile):
                try:
                    if r'%%volume' in pattern:        # %%paramaters are global parameters
                        globalvolume *= 10 ** (float(pattern.split('=')[1].strip()) / 20)
                        continue
                    if r'%%transpose' in pattern:
                        globaltranspose = int(pattern.split('=')[1].strip())
                        continue
                    #--- ADD THIS PART ---
                    if r'%%mode' in pattern:
                        mode_val = pattern.split('=')[1].strip().lower()
                        oneshot_mode = (mode_val == 'oneshot')
                        print("One Shot Sample")
                        continue
                    defaultparams = {'midinote': '0', 'velocity': '127', 'notename': ''}
                    if len(pattern.split(',')) > 1:
                        defaultparams.update(dict([item.split('=') for item in pattern.split(',', 1)[1].replace(' ', '').replace('%', '').split(',')]))
                    pattern = pattern.split(',')[0]
                    pattern = re.escape(pattern.strip())  # note for Python 3.7+: "%" is no longer escaped with "\"
                    pattern = pattern.replace(r"%midinote", r"(?P<midinote>\d+)").replace(r"%velocity", r"(?P<velocity>\d+)")\
                                     .replace(r"%notename", r"(?P<notename>[A-Ga-g]#?[0-9])").replace(r"\*", r".*?").strip()    # .*? => non greedy
                    for fname in os.listdir(dirname):
                        if LoadingInterrupt:
                            return
                        m = re.match(pattern, fname)
                        if m:
                            info = m.groupdict()
                            midinote = int(info.get('midinote', defaultparams['midinote']))
                            velocity = int(info.get('velocity', defaultparams['velocity']))
                            notename = info.get('notename', defaultparams['notename'])
                            if notename:
                                midinote = NOTES.index(notename[:-1].lower()) + (int(notename[-1])+2) * 12
                            samples[midinote, velocity] = Sound(os.path.join(dirname, fname), midinote, velocity)
                except:
                    print("Error in definition file, skipping line %s." % (i+1))
    else:
        for midinote in range(0, 127):
            if LoadingInterrupt:
                return
            file = os.path.join(dirname, "%d.wav" % midinote)
            if os.path.isfile(file):
                samples[midinote, 127] = Sound(file, midinote, 127)
    initial_keys = set(samples.keys())
    for midinote in range(128):
        lastvelocity = None
        for velocity in range(128):
            if (midinote, velocity) not in initial_keys:
                samples[midinote, velocity] = lastvelocity
            else:
                if not lastvelocity:
                    for v in range(velocity):
                        samples[midinote, v] = samples[midinote, velocity]
                lastvelocity = samples[midinote, velocity]
        if not lastvelocity:
            for velocity in range(128):
                try:
                    samples[midinote, velocity] = samples[midinote-1, velocity]
                except:
                    pass
    if len(initial_keys) > 0:
        print('Preset loaded: ' + str(preset))
        display("%04d" % preset)
    else:
        print('Preset empty: ' + str(preset))
        display("E%03d" % preset)

#########################################
# OPEN AUDIO DEVICE
#
#########################################

try:
    sd = sounddevice.OutputStream(device=AUDIO_DEVICE_ID, blocksize=512, samplerate=44100, channels=2, dtype='int16', callback=AudioCallback)
    sd.start()
    print('Opened audio device #%i' % AUDIO_DEVICE_ID)
except:
    print('Invalid audio device #%i' % AUDIO_DEVICE_ID)
    exit(1)

#########################################
# BUTTONS THREAD (RASPBERRY PI GPIO)
#
#########################################

import RPi.GPIO as GPIO
import time
import threading

BUTTON_PINS = [4, 17, 27, 5, 6, 13, 25, 8, 7] # Changed last '8' to '7' as a guess
MIDI_NOTES = [36, 38, 40, 41, 43, 45, 47, 48, 50]

button_states = {pin: False for pin in BUTTON_PINS}

def play_note(midinote, velocity=127):
    global playingnotes, samples

    # 1. Calculate Status Byte based on MIDI_CHANNEL (Omni defaults to Ch 1 for output)
    out_channel = MIDI_CHANNEL if MIDI_CHANNEL != 0 else 1
    status_byte = 0x90 | (out_channel - 1)

    # 2. Internal trigger
    MidiCallback([status_byte, midinote, velocity], None)

    # 3. EXTERNAL trigger (Sends to MIDI OUT)
    if USE_SERIALPORT_MIDI:
        try:
            ser.write(bytearray([status_byte, midinote, velocity]))
        except:
            pass

    # 4. Local sample playback
    if (midinote, velocity) in samples:
        playingnotes.setdefault(midinote, []).append(samples[midinote, velocity].play(midinote))
    elif (midinote, 127) in samples:
        playingnotes.setdefault(midinote, []).append(samples[midinote, 127].play(midinote))

def stop_note(midinote):
    global playingnotes

    # 1. Calculate Status Byte for Note Off (0x80)
    out_channel = MIDI_CHANNEL if MIDI_CHANNEL != 0 else 1
    status_byte = 0x80 | (out_channel - 1)

    # 2. Internal trigger
    MidiCallback([status_byte, midinote, 0], None)

    # 3. EXTERNAL trigger
    if USE_SERIALPORT_MIDI:
        try:
            ser.write(bytearray([status_byte, midinote, 0]))
        except:
            pass

    # 4. Local sample stop
    if midinote in playingnotes:
        for n in playingnotes[midinote]:
            if hasattr(n, 'fadeout'):
                n.fadeout(50)
        playingnotes[midinote] = []

def Buttons():
    GPIO.setmode(GPIO.BCM)
    for pin in BUTTON_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    while True:
        for i, pin in enumerate(BUTTON_PINS):
            is_pressed = not GPIO.input(pin)
            midinote = MIDI_NOTES[i]

            if is_pressed and not button_states[pin]:
                print(f"Triggering Note: {midinote}") # Debug line
                play_note(midinote)
                button_states[pin] = True
            elif not is_pressed and button_states[pin]:
                # Only call stop_note if the folder isn't a drum kit
                if not oneshot_mode:
                    stop_note(midinote)
                button_states[pin] = False
        time.sleep(0.01)

# Start the thread
ButtonsThread = threading.Thread(target=Buttons)
ButtonsThread.daemon = True
ButtonsThread.start()



if USE_BUTTONS:
    import RPi.GPIO as GPIO
    lastbuttontime = 0
    def Buttons():
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(18, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        global preset, lastbuttontime
        while True:
            now = time.time()
            if not GPIO.input(18) and (now - lastbuttontime) > 0.2:
                lastbuttontime = now
                preset -= 1
                if preset < 0:
                    preset = 127
                LoadSamples()
            elif not GPIO.input(17) and (now - lastbuttontime) > 0.2:
                lastbuttontime = now
                preset += 1
                if preset > 127:
                    preset = 0
                LoadSamples()
            time.sleep(0.020)
    ButtonsThread = threading.Thread(target=Buttons)
    ButtonsThread.daemon = True
    ButtonsThread.start()

#########################################
# ENCODER
#
#########################################

if USE_ENCODER:
    import RPi.GPIO as GPIO
    import threading
    import time

    def EncoderProcess():
        # MOSI = 10, MISO = 9
        ENC_A, ENC_B = 10, 9
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        global preset

        # State tracking
        outcome = [0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0]
        last_AB = (GPIO.input(ENC_A) << 1) | GPIO.input(ENC_B)
        counter = 0

        # Max folder index (0 and 1)
        MAX_PRESET = 127

        while True:
            # Create a 2-bit number from Pin A and B
            current_A = GPIO.input(ENC_A)
            current_B = GPIO.input(ENC_B)
            current_AB = (current_A << 1) | current_B

            if current_AB != last_AB:
                # Use a state table to determine direction and ignore "half-steps"
                # This transition index (last_AB + current_AB) filters noise
                transition = (last_AB << 2) | current_AB
                counter += outcome[transition]

                # Standard encoders usually need 2 or 4 "state changes" to make 1 click
                # Change the '4' below to '2' if it becomes too slow
                if abs(counter) >= 4:
                    if counter > 0:
                        preset += 1
                    else:
                        preset -= 1

                    # Boundary handling
                    if preset > MAX_PRESET: preset = 0
                    elif preset < 0: preset = MAX_PRESET

                    print(f"ENCODER: Click Confirmed. New Preset: {preset}")
                    LoadSamples()

                    counter = 0 # Reset for next physical click

                last_AB = current_AB

            time.sleep(0.001)

    threading.Thread(target=EncoderProcess, daemon=True).start()


#########################################
# 7-SEGMENT DISPLAY
#
#########################################

if USE_I2C_7SEGMENTDISPLAY:  # requires: 1) i2c-dev in /etc/modules and 2) dtparam=i2c_arm=on in /boot/config.txt
    import smbus
    bus = smbus.SMBus(1)     # using I2C
    def display(s):
        for k in '\x76\x79\x00' + s:     # position cursor at 0
            try:
                bus.write_byte(0x71, ord(k))
            except:
                try:
                    bus.write_byte(0x71, ord(k))
                except:
                    pass
            time.sleep(0.002)
    display('----')
    time.sleep(0.5)
else:
    def display(s):
        pass

#########################################
# SSD1306 Display
#
#########################################

if USE_SSD1306:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from luma.core.render import canvas
    from PIL import ImageFont

    try:
        # Standard I2C setup for SSD1306
        serial = i2c(port=1, address=0x3C)
        device = ssd1306(serial, width=128, height=32)
        font = ImageFont.load_default()
    except Exception as e:
        print(f"OLED Hardware Error: {e}")
        device = None

    def DisplayUpdate(text1, text2=""):
        if device:
            with canvas(device) as draw:
                # Top line: Title
                draw.text((0, 0), "SAMPLERBOX", font=font, fill="white")
                # Middle line: Current Preset Number/Name
                draw.text((0, 20), f"> {text1}", font=font, fill="white")
                # Bottom line: Optional status
                draw.text((0, 45), text2, font=font, fill="white")



#########################################
# MIDI IN via SERIAL PORT
#########################################

if USE_SERIALPORT_MIDI:
    import serial
    # Use 38400 here! The midi-uart0 overlay converts it to 31250.
    ser = serial.Serial('/dev/ttyAMA0', baudrate=38400)

    def MidiSerialCallback():
        message = [0, 0, 0]
        while True:
            try:
                data = ord(ser.read(1))

                # Skip MIDI Real-time/Active Sensing (248-254)
                if data >= 0xF8:
                    continue

                # Status byte found (128-239)
                if data & 0x80:
                    message[0] = data
                    message[1] = ord(ser.read(1))

                    # Program change (0xC0) and Aftertouch (0xD0) are 2-byte
                    if (data & 0xF0) != 0xC0 and (data & 0xF0) != 0xD0:
                        message[2] = ord(ser.read(1))
                    else:
                        message[2] = 0

                    MidiCallback(message, None)

            except Exception as e:
                print(f"Serial Error: {e}")

    # --- THIS PART WAS MISSING OR MISALIGNED ---
    MidiThread = threading.Thread(target=MidiSerialCallback)
    MidiThread.daemon = True
    MidiThread.start()
    print("Serial MIDI Thread Started on /dev/ttyAMA0")

#########################################
# LOAD FIRST SOUNDBANK
#
#########################################

preset = 0
LoadSamples()

#########################################
# SYSTEM LED
#
#########################################
if USE_SYSTEMLED:
    os.system("modprobe ledtrig_heartbeat")
    os.system("echo heartbeat >/sys/class/leds/led0/trigger")

#########################################
# MIDI DEVICES DETECTION
# MAIN LOOP
#########################################

midi_in = [rtmidi.MidiIn(b'in')]
previous = []
while True:
    for port in midi_in[0].ports:
        #print(midi_in[0].ports)
        if port not in previous and b'Midi Through' not in port:
            midi_in.append(rtmidi.MidiIn(b'in'))
            midi_in[-1].callback = MidiCallback
            midi_in[-1].open_port(port)
            print('Opened MIDI: ' + str(port))
    previous = midi_in[0].ports
    time.sleep(2)
