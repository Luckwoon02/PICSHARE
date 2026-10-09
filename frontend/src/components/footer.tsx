export function Footer() {
    return (
        <footer className="border-t border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div className="container mx-auto flex h-14 items-center flex-col md:flex-row justify-between">
                <p className="text-sm text-muted-foreground">
                    © 2026 Pixello AI. All rights reserved.
                </p>
                <p className="md:pb-0 pb-2 text-sm text-muted-foreground">
                    Made with <span className="text-red-500 animate-pulse">❤️</span> by{" "}
                    <span className="font-medium text-foreground">
                        Kaushik Ghosh
                    </span>
                </p>
            </div>
        </footer>
    );
}
