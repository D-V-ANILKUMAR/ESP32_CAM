/*
 * ESP32-CAM – Smart Robotic Arm Image Sender
 *
 * This sketch:
 *   1. Connects to Wi-Fi.
 *   2. Sends periodic heartbeat pings to the Flask server.
 *   3. Reads a force sensor / limit switch to detect object pickup.
 *   4. Captures an image with the OV2640 camera and POSTs it to the server.
 *
 * Board: AI Thinker ESP32-CAM
 * Required library: HTTPClient (bundled with ESP32 Arduino core)
 *
 * ⚠️  Update the Wi-Fi credentials and server IP below before uploading.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// ======================== USER CONFIG ========================
const char* ssid          = "YOUR_WIFI_SSID";
const char* password      = "YOUR_WIFI_PASSWORD";
const char* serverAddress = "Server IP Address";  // Flask server IP

// Force sensor / limit switch pin
#define PICKUP_SENSOR_PIN 13   // GPIO 13 – change as needed
// =============================================================

// AI-Thinker ESP32-CAM pin definitions
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// Timing
unsigned long lastHeartbeat   = 0;
const unsigned long heartbeatInterval = 5000;  // 5 seconds

bool lastPickupState = false;

// -----------------------------------------------------------------
void initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Adjust quality based on PSRAM availability
  if (psramFound()) {
    config.frame_size   = FRAMESIZE_UXGA;   // 1600×1200
    config.jpeg_quality = 10;
    config.fb_count     = 2;
  } else {
    config.frame_size   = FRAMESIZE_SVGA;   // 800×600
    config.jpeg_quality = 12;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    ESP.restart();
  }
  Serial.println("Camera initialised ✓");
}

// -----------------------------------------------------------------
void sendHeartbeat() {
  HTTPClient http;
  String url = String(serverAddress) + "/api/heartbeat";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST("{}");
  Serial.printf("Heartbeat → %d\n", code);
  http.end();
}

// -----------------------------------------------------------------
void sendPickupStatus(const char* status) {
  HTTPClient http;
  String url = String(serverAddress) + "/api/pickup_status";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"pickup_status\":\"") + status + "\"}";
  int code = http.POST(body);
  Serial.printf("Pickup status '%s' → %d\n", status, code);
  http.end();
}

// -----------------------------------------------------------------
void captureAndSend() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed");
    return;
  }

  HTTPClient http;
  String url = String(serverAddress) + "/api/upload";
  http.begin(url);

  // Build multipart/form-data body manually
  String boundary = "----ESP32CAMBoundary";
  String head  = "--" + boundary + "\r\n"
                 "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n"
                 "Content-Type: image/jpeg\r\n\r\n";
  String field = "\r\n--" + boundary + "\r\n"
                 "Content-Disposition: form-data; name=\"pickup_status\"\r\n\r\nPicked";
  String tail  = "\r\n--" + boundary + "--\r\n";

  uint32_t totalLen = head.length() + fb->len + field.length() + tail.length();
  uint8_t* payload = (uint8_t*)malloc(totalLen);
  if (!payload) {
    Serial.println("malloc failed");
    esp_camera_fb_return(fb);
    return;
  }

  uint32_t offset = 0;
  memcpy(payload + offset, head.c_str(), head.length());           offset += head.length();
  memcpy(payload + offset, fb->buf, fb->len);                      offset += fb->len;
  memcpy(payload + offset, field.c_str(), field.length());         offset += field.length();
  memcpy(payload + offset, tail.c_str(), tail.length());           offset += tail.length();

  http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
  int code = http.POST(payload, totalLen);
  Serial.printf("Image upload → %d\n", code);

  free(payload);
  esp_camera_fb_return(fb);
  http.end();
}

// -----------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.println("\n========== ESP32-CAM Robotic Arm ==========");

  // Sensor pin
  pinMode(PICKUP_SENSOR_PIN, INPUT_PULLUP);

  // Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nConnected!  IP: %s\n", WiFi.localIP().toString().c_str());

  // Camera
  initCamera();

  // Initial heartbeat
  sendHeartbeat();
}

// -----------------------------------------------------------------
void loop() {
  // Periodic heartbeat
  if (millis() - lastHeartbeat >= heartbeatInterval) {
    sendHeartbeat();
    lastHeartbeat = millis();
  }

  // Read force sensor / limit switch (LOW = object picked with INPUT_PULLUP)
  bool picked = (digitalRead(PICKUP_SENSOR_PIN) == LOW);

  if (picked && !lastPickupState) {
    // Rising edge – object just picked
    Serial.println("Object PICKED – capturing image…");
    sendPickupStatus("Picked");
    delay(300);  // small settle time
    captureAndSend();
  } else if (!picked && lastPickupState) {
    // Object released
    Serial.println("Object released");
    sendPickupStatus("Not Picked");
  }

  lastPickupState = picked;
  delay(100);
}
