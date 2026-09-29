package com.bmc.arsys.api;

import java.util.List;

public final class Menu {
    private final int menuType;
    private final List<MenuItem> content;

    public Menu(int menuType, List<MenuItem> content) {
        this.menuType = menuType;
        this.content = content;
    }

    public int getMenuType() {
        return menuType;
    }

    public List<MenuItem> getContent() {
        return content;
    }
}
