# Claude Micro
# The joystick click needs a GPIO of its own, so the matrix is scanned here
# (see matrix.c) rather than by the stock routine.
CUSTOM_MATRIX = lite
SRC += matrix.c

ANALOG_DRIVER_REQUIRED = yes
RAW_ENABLE = yes
