// 25kHz PWM GPU Fan Controller via Serial
// Receives PWM duty cycle (0-255) over USB Serial and outputs 25kHz PWM on D9

const int PWM_PIN = 9;
const unsigned long TIMEOUT_MS = 3000;  // Fallback after 3s of no data (PC polls every 0.5s)

// Default safe duty cycle (170/255 = ~67%) — matches user's current setting
const uint8_t DEFAULT_DUTY = 170;

volatile uint8_t current_duty = DEFAULT_DUTY;
volatile unsigned long last_rx_time = 0;

void setup() {
  Serial.begin(115200);

  // Configure Timer1 for 25kHz Fast PWM on pin 9 (OCR1A)
  // Mode 14 (Fast PWM, ICR1 as TOP), prescaler 1
  // f_PWM = 16MHz / (1 * (ICR1 + 1))
  // For 25kHz: ICR1 = 16,000,000 / 25,000 - 1 = 639
  pinMode(PWM_PIN, OUTPUT);
  TCCR1A = _BV(COM1A1) | _BV(WGM11);                     // Non-inverting, Fast PWM (mode 14)
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS10);          // Mode 14, prescaler 1
  ICR1 = 639;                                             // TOP = 639 → 25kHz
  OCR1A = map(DEFAULT_DUTY, 0, 255, 0, 639);             // Set initial duty

  Serial.println("GPU Fan Controller ready");
}

void loop() {
  // Read incoming PWM value (0-255) as a single byte
  if (Serial.available()) {
    uint8_t val = Serial.read();
    set_pwm(val);
    last_rx_time = millis();
    // Echo back for confirmation
    Serial.println(val);
  }

  // Fallback: if no data received for TIMEOUT_MS, revert to default
  if (millis() - last_rx_time > TIMEOUT_MS && current_duty != DEFAULT_DUTY) {
    set_pwm(DEFAULT_DUTY);
  }
}

void set_pwm(uint8_t duty) {
  current_duty = duty;
  // Scale 0-255 → 0-639 (ICR1 range) for 25kHz PWM
  OCR1A = map(duty, 0, 255, 0, 639);
}
