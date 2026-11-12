# Documentation screenshots

`configuration-dashboard.png` shows the real Dashboard HTML and JavaScript
rendered in Chromium with fictional demonstration responses. Form names,
credential availability, connection checks and update status are examples, not
observations from a live installation. No credential values are supplied.

The capture uses only the repository's public default policy and UI resources.
Every browser request is intercepted; unexpected requests fail the capture.
It does not start a Gateway, contact Helix, read an installed configuration,
save settings or install an update. No redaction or image editing is required.

To reproduce in a development virtual environment from the repository root:

```sh
python -m pip install -e ".[test,browser-test]"
python -m playwright install chromium
python docs/images/capture_dashboard.py
```

Review the generated image before committing it. Keep all demonstration names
fictional and retain the screenshot's explanatory caption in the main README.
