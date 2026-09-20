## Fix

- Preserve multiline Telegram notification text by keeping multipart form values out of the `curl -K` configuration file, preventing curl configuration parsing failures while retaining token redaction guarantees.
