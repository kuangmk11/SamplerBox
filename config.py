#########################################
# LOCAL
# CONFIG
#########################################

AUDIO_DEVICE_ID = 0                     # change this number to use another soundcard
SAMPLES_DIR = "/media/"                 # The root directory containing the sample-sets. Example: "/media/" to look for samples on a USB stick / SD card
MAX_POLYPHONY = 80                      # This can be set higher, but 80 is a safe value
USE_BUTTONS = False                      # Set to True to use momentary buttons (connected to RaspberryPi's GPIO pins 17 and 18) to change preset
USE_I2C_7SEGMENTDISPLAY = False          # Set to True to use a 7-segment display via I2C
USE_SERIALPORT_MIDI = True              # Set to True to enable MIDI IN via SerialPort (e.g. RaspberryPi's GPIO UART pins)
USE_SYSTEMLED = True                    # Flashing LED after successful boot, only works on RPi/Linux
USE_ENCODER = True                      # Set to True to use MOSI/MISO encoder
USE_SSD1306 = True
preset = 0                               # Initialize here so DisplayUpdate can find it on boot
MIDI_CHANNEL = 0                         # Use 0 for OMNI (receive on all channels), or 1-16 for a specific channel
