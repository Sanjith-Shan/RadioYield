idf_component_register(
    SRCS "main.c" "console.c" "rx.c" "radio_wifi.c" "radio_ble.c" "radio_154.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES ry_codec nvs_flash esp_wifi esp_netif esp_event esp_timer bt ieee802154
                  esp_driver_usb_serial_jtag esp_driver_uart vfs)
