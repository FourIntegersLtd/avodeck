# ClamAV for virus scanning (ADR 0027): the official image plus the size limits that match the upload
# maximum (NFR C1, 500 MB). Pin the digest of this tag in the dashboard build once the first image is chosen.
FROM clamav/clamav:1.4

# Streamed in, verdict out: a file larger than these is blocked, not passed (AlertExceedsMax).
ENV CLAMD_CONF_StreamMaxLength=500M \
    CLAMD_CONF_MaxScanSize=500M \
    CLAMD_CONF_MaxFileSize=500M \
    CLAMD_CONF_AlertExceedsMax=yes \
    CLAMD_CONF_MaxThreads=4 \
    CLAMD_CONF_TCPSocket=3310 \
    CLAMD_CONF_TCPAddr=::
