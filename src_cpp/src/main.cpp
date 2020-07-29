/*******************************************************************************
  BME280 & DS18B20 logger
  Adafruit Feather M4

  BME280:
    Reads out temperature, humidity and pressure over I2C.
    Pins: SDA & SCL
  DS18B20:
    Temperature
    Pins: DI5

  The RGB LED of the Feather M4 will indicate its status:
  * Blue : We're setting up
  * Green: Running okay
  Every read out, the LED will alternate in brightness high/low

  Dennis van Gils
  29-07-2020
*******************************************************************************/

#include <Arduino.h>
#include "DvG_SerialCommand.h"
#include "Adafruit_NeoPixel.h"

// DS18B20
#include <OneWire.h>
#include <DallasTemperature.h>

// BME280
#include "BME280I2C.h"
#include "Wire.h"
#include "SPI.h"

DvG_SerialCommand sc(Serial); // Instantiate serial command listener
Adafruit_NeoPixel strip(1, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

#define PIN_DS18B20 5
OneWire oneWire(PIN_DS18B20);
DallasTemperature ds18(&oneWire);
BME280I2C bme;

// -----------------------------------------------------------------------------
//    setup
// -----------------------------------------------------------------------------

void setup() {
    strip.begin();
    strip.setPixelColor(0, strip.Color(0, 0, 15)); // Blue: We're in setup()
    strip.show();

    Serial.begin(9600);

    // DS18B20
    ds18.begin();

    // BME280
    Wire.begin();
    while (!bme.begin()) {
        Serial.println("Could not find a valid BME280 sensor, check wiring!");
        delay(1000);
    }

    strip.setPixelColor(0, strip.Color(0, 15, 0)); // Green: All set up
    strip.show();
}

// -----------------------------------------------------------------------------
//    loop
// -----------------------------------------------------------------------------

bool toggle = false;
float ds18_temp(NAN);     // ['C]
float bme280_temp(NAN);   // ['C]
float bme280_humi(NAN);   // [%]
float bme280_pres(NAN);   // [Pa]

BME280::TempUnit tempUnit(BME280::TempUnit_Celsius);
BME280::PresUnit presUnit(BME280::PresUnit_Pa);

void loop() {
    char *strCmd; // Incoming serial command string
    uint32_t now;

    if (sc.available()) {
        strCmd = sc.getCmd();

        if (strcmp(strCmd, "id?") == 0) {
            Serial.println("Arduino, BME280 & DS18B20 logger");

        } else {
            toggle = !toggle;
            if (toggle) {
                strip.setPixelColor(0, strip.Color(0, 5, 0));
            } else {
                strip.setPixelColor(0, strip.Color(0, 15, 0));
            }
            strip.show();

            now = millis();
            ds18.requestTemperatures();
            ds18_temp = ds18.getTempCByIndex(0);
            bme.read(bme280_pres, bme280_temp, bme280_humi, tempUnit, presUnit);

            Serial.println(
                String(now) +
                '\t' + String(ds18_temp, 1) +
                '\t' + String(bme280_temp, 1) +
                '\t' + String(bme280_humi, 1) +
                '\t' + String(bme280_pres, 0));
        }
    }
}
