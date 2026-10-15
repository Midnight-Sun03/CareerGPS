document.addEventListener("DOMContentLoaded", () => {
    const menu = document.querySelector(
        "[data-notification-menu]"
    );

    if (!menu) {
        return;
    }

    const toggle = menu.querySelector(
        "[data-notification-toggle]"
    );

    const dropdown = menu.querySelector(
        "[data-notification-dropdown]"
    );

    if (!toggle || !dropdown) {
        return;
    }


    function openDropdown() {
        dropdown.hidden = false;

        toggle.setAttribute(
            "aria-expanded",
            "true"
        );
        const firstAction = dropdown.querySelector("button, a");
        if (firstAction) firstAction.focus();
    }


    function closeDropdown(restoreFocus = false) {
        const wasOpen = !dropdown.hidden;
        dropdown.hidden = true;

        toggle.setAttribute(
            "aria-expanded",
            "false"
        );
        if (restoreFocus && wasOpen) toggle.focus();
    }


    function toggleDropdown() {
        if (dropdown.hidden) {
            openDropdown();
        } else {
            closeDropdown();
        }
    }


    toggle.addEventListener(
        "click",
        (event) => {
            event.stopPropagation();

            toggleDropdown();
        }
    );


    dropdown.addEventListener(
        "click",
        (event) => {
            event.stopPropagation();
        }
    );


    document.addEventListener(
        "click",
        () => {
            closeDropdown();
        }
    );


    document.addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Escape") {
                closeDropdown(true);
            }
        }
    );
});
