"use client";

import { usePathname } from "next/navigation";

export function Footer() {
    const pathname = usePathname();
    if (pathname.startsWith("/select/")) return null;

    return (
        <footer className="border-t border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div className="container mx-auto flex h-14 items-center flex-col md:flex-row justify-between">
                <p className="text-sm text-muted-foreground">
                    © 2026 Pixello AI. All rights reserved.
                </p>
            </div>
        </footer>
    );
}
