package com.bmc.arsys.api;

public final class AttachmentFieldLimit extends FieldLimit {
    public int getMaxSize() { return 4096; }
    public int getAttachType() { return 0; }
    public int getFullTextOption() { return 0; }
}
