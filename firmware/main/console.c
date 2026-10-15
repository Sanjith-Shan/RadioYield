/* Console transport: line-buffered stdin/stdout over USB-Serial-JTAG (default on
 * ESP32-C6) or the UART console (boards with a USB-UART bridge), via the blocking
 * VFS drivers so fgets() blocks and printf() never interleaves mid-line. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ry_app.h"

#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#elif CONFIG_ESP_CONSOLE_UART
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#else
#error "RadioYield needs CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG or a UART console"
#endif

static SemaphoreHandle_t s_out_lock;

void ry_console_init(void)
{
    s_out_lock = xSemaphoreCreateMutex();
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.rx_buffer_size = 1024;
    cfg.tx_buffer_size = 4096;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    usb_serial_jtag_vfs_set_rx_line_endings(ESP_LINE_ENDINGS_LF); /* no conversion; parser strips CR */
    usb_serial_jtag_vfs_set_tx_line_endings(ESP_LINE_ENDINGS_LF);
    usb_serial_jtag_vfs_use_driver();
#else
    ESP_ERROR_CHECK(uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, 1024, 4096, 0, NULL, 0));
    uart_vfs_dev_port_set_rx_line_endings(CONFIG_ESP_CONSOLE_UART_NUM, ESP_LINE_ENDINGS_LF);
    uart_vfs_dev_port_set_tx_line_endings(CONFIG_ESP_CONSOLE_UART_NUM, ESP_LINE_ENDINGS_LF);
    uart_vfs_dev_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);
#endif
    setvbuf(stdin, NULL, _IONBF, 0);
}

void ry_out(const char *fmt, ...)
{
    char line[RY_LINE_MAX + 64];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n > sizeof(line) - 2) n = (int)sizeof(line) - 2;
    line[n++] = '\n';
    line[n] = 0;
    xSemaphoreTake(s_out_lock, portMAX_DELAY);
    fputs(line, stdout);
    fflush(stdout);
    xSemaphoreGive(s_out_lock);
}

int ry_console_readline(char *buf, size_t buflen)
{
    for (;;) {
        if (!fgets(buf, (int)buflen, stdin)) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        size_t n = strlen(buf);
        if (n > 0 && buf[n - 1] == '\n') return (int)n;
        if (n + 1 < buflen) return (int)n; /* EOF without newline: treat as a line */
        /* over-long: swallow the remainder up to the newline */
        int c;
        while ((c = fgetc(stdin)) != '\n' && c != EOF) {
        }
        return -1;
    }
}
