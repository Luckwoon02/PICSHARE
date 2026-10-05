"use client";

import React, { useState, useRef, useCallback, useEffect } from "react";
import { useParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Camera, Upload, CheckCircle2, Loader2, RefreshCw, XCircle, Lock, UserPlus, ArrowRight, Trash2 } from "lucide-react";
import { toast } from "sonner";
import Webcam from "react-webcam";

export default function GuestUploadClient() {
    const params = useParams();
    const slug = params.slug as string;
    const webcamRef = useRef<Webcam>(null);

    const [loading, setLoading] = useState(false);
    const [success, setSuccess] = useState(false);
    const [fileName, setFileName] = useState<string | null>(null);
    const [file, setFile] = useState<File | null>(null);
    const [capturedImage, setCapturedImage] = useState<string | null>(null);
    const [isCameraActive, setIsCameraActive] = useState(true);
    const [requestId, setRequestId] = useState<string | null>(null);
    const [polling, setPolling] = useState(false);
    const [matchCount, setMatchCount] = useState(0);

    const [eventInfo, setEventInfo] = useState<{ name: string; is_protected: boolean } | null>(null);
    const [secretCode, setSecretCode] = useState("");
    const [isVerified, setIsVerified] = useState(false);
    const [checkingEvent, setCheckingEvent] = useState(true);
    const [savedGuests, setSavedGuests] = useState<{ id: string; name: string; email?: string }[]>([]);
    const [showUploadForm, setShowUploadForm] = useState(false);

    useEffect(() => {
        if (typeof window !== "undefined" && window.location.protocol === "http:" && window.location.hostname !== "localhost") {
            toast.warning("Camera access requires HTTPS. If the camera doesn't show, please use the 'Switch to Upload' option.", {
                duration: 6000
            });
        }

        // Check if code exists in session
        const savedCode = sessionStorage.getItem(`event_code_${slug}`);
        if (savedCode) {
            setSecretCode(savedCode);
        }

        // Load saved guests for this event
        const saved = localStorage.getItem(`guests_${slug}`);
        if (saved) {
            const parsed = JSON.parse(saved);
            setSavedGuests(parsed);
            if (parsed.length > 0) {
                setShowUploadForm(false);

                // Verify if they still exist in DB
                const verifySavedGuests = async () => {
                    try {
                        const apiUrl = process.env.NEXT_PUBLIC_API_URL;
                        const results = await Promise.all(
                            parsed.map(async (g: { id: string }) => {
                                try {
                                    const res = await fetch(`${apiUrl}/guests/status/${g.id}`);
                                    return { id: g.id, exists: res.ok };
                                } catch {
                                    return { id: g.id, exists: true }; // Keep on network error
                                }
                            })
                        );

                        const existingIds = results.filter(r => r.exists).map(r => r.id);
                        const validated = parsed.filter((g: { id: string }) => existingIds.includes(g.id));

                        if (validated.length !== parsed.length) {
                            setSavedGuests(validated);
                            localStorage.setItem(`guests_${slug}`, JSON.stringify(validated));
                            if (validated.length === 0) {
                                setShowUploadForm(true);
                            }
                            toast.info("Information: Some previously saved faces have been removed as they are no longer in the system.");
                        }
                    } catch (err) {
                        console.error("Error verifying guests:", err);
                    }
                };
                verifySavedGuests();
            } else {
                setShowUploadForm(true);
            }
        } else {
            setShowUploadForm(true);
        }

        // Fetch event info
        const fetchEventInfo = async () => {
            try {
                const apiUrl = process.env.NEXT_PUBLIC_API_URL;
                const res = await fetch(`${apiUrl}/events/public/${slug}`);
                if (res.ok) {
                    const data = await res.json();
                    setEventInfo(data);

                    // Auto-verify if no protection OR if we have a saved code
                    if (!data.is_protected) {
                        setIsVerified(true);
                    } else if (savedCode) {
                        setIsVerified(true);
                    }
                } else {
                    toast.error("Event not found");
                }
            } catch (err) {
                console.error("Fetch event info error:", err);
            } finally {
                setCheckingEvent(false);
            }
        };
        fetchEventInfo();
    }, [slug]);

    const videoConstraints = {
        width: 720,
        height: 720,
        facingMode: "user"
    };

    const capture = useCallback(() => {
        const imageSrc = webcamRef.current?.getScreenshot();
        if (imageSrc) {
            setCapturedImage(imageSrc);

            // Convert base64 to File object
            const byteString = atob(imageSrc.split(',')[1]);
            const mimeString = imageSrc.split(',')[0].split(':')[1].split(';')[0];
            const ab = new ArrayBuffer(byteString.length);
            const ia = new Uint8Array(ab);
            for (let i = 0; i < byteString.length; i++) {
                ia[i] = byteString.charCodeAt(i);
            }
            const blob = new Blob([ab], { type: mimeString });
            const selfieFile = new File([blob], "selfie.jpg", { type: "image/jpeg" });

            setFile(selfieFile);
            setFileName("captured-selfie.jpg");
        }
    }, [webcamRef]);

    const retake = () => {
        setCapturedImage(null);
        setFile(null);
        setFileName(null);
    };

    // Polling for guest request status
    useEffect(() => {
        let interval: NodeJS.Timeout;
        if (polling && requestId) {
            interval = setInterval(async () => {
                try {
                    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
                    const res = await fetch(`${apiUrl}/guests/status/${requestId}`);
                    if (res.ok) {
                        const data = await res.json();

                        if (data.status === "completed") {
                            setMatchCount(data.match_count || 0);
                            setSuccess(true);
                            setPolling(false);
                            setLoading(false);

                            // Save to local storage
                            const currentSaved = JSON.parse(localStorage.getItem(`guests_${slug}`) || "[]");

                            // Check if already exists
                            if (!currentSaved.some((g: { id: string }) => g.id === requestId)) {
                                const newGuest = { id: requestId, name: `Face ${currentSaved.length + 1}` };
                                const updated = [...currentSaved, newGuest];
                                localStorage.setItem(`guests_${slug}`, JSON.stringify(updated));
                                setSavedGuests(updated);
                            }
                        }
                        else if (data.status === "error") {
                            setPolling(false);
                            setLoading(false);
                            toast.error(data.error || "Processing failed");
                        }
                    }
                } catch (e) {
                    console.error("Polling error:", e);
                }
            }, 3000);
        }
        return () => clearInterval(interval);
    }, [polling, requestId, slug]);

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (!file) {
            toast.error("Please provide a selfie first.");
            return;
        }

        setLoading(true);
        const formData = new FormData();

        formData.append("event_slug", slug);
        formData.append("selfie", file);
        if (secretCode) {
            formData.append("secret_code", secretCode);
        }

        try {
            const apiUrl = process.env.NEXT_PUBLIC_API_URL
            const response = await fetch(`${apiUrl}/guests/request`, {
                method: "POST",
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || "Failed to upload");
            }

            const data = await response.json();
            setRequestId(data.request_id);
            setPolling(true);

            // If the request was already completed, it will return the existing status
            if (data.status === "completed") {
                // Handled in polling effect or we can handle here too
            }

            toast.info("Processing your photos... this may take a moment.");
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Something went wrong. Please try again.";
            toast.error(message);
            setLoading(false);
        }
    };

    const removeSavedGuest = (id: string) => {
        const updated = savedGuests.filter(g => g.id !== id);
        setSavedGuests(updated);
        localStorage.setItem(`guests_${slug}`, JSON.stringify(updated));
        if (updated.length === 0) {
            setShowUploadForm(true);
        }
        toast.success("Face removed from this device");
    };

    const handleVerifyEmailCode = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!secretCode) return;

        setLoading(true);
        try {
            const apiUrl = process.env.NEXT_PUBLIC_API_URL;
            const res = await fetch(`${apiUrl}/events/verify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ slug, code: secretCode })
            });

            if (res.ok) {
                setIsVerified(true);
                sessionStorage.setItem(`event_code_${slug}`, secretCode);
            } else {
                toast.error("Invalid secret code");
            }
        } catch (err: unknown) {
            console.error("Verification error:", err);
            toast.error("Verification failed");
        } finally {
            setLoading(false);
        }
    };

    if (checkingEvent) {
        return (
            <div className="min-h-screen bg-background flex items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-indigo-600" />
            </div>
        );
    }

    if (!eventInfo) {
        return (
            <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
                <Card className="w-full max-w-md border-none shadow-xl bg-card/80 backdrop-blur-md text-center p-6">
                    <XCircle className="w-12 h-12 text-red-500 mx-auto mb-4" />
                    <h2 className="text-xl font-bold text-foreground mb-2">Event Not Found</h2>
                    <p className="text-muted-foreground mb-6">We couldn&apos;t load the event details. Please check the URL or try again later.</p>
                    <Button onClick={() => window.location.reload()} variant="outline">Retry</Button>
                </Card>
            </div>
        );
    }

    if (eventInfo?.is_protected && !isVerified) {
        return (
            <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4 bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-blue-100/20 via-background to-indigo-100/20 text-foreground transition-colors duration-300">
                <Card className="w-full max-w-md border border-border shadow-2xl bg-card/90 backdrop-blur-lg">
                    <CardHeader className="text-center">
                        <div className="mx-auto w-12 h-12 bg-amber-100 dark:bg-amber-900/30 rounded-full flex items-center justify-center mb-4 text-amber-600 dark:text-amber-500">
                            <Lock className="w-6 h-6" />
                        </div>
                        <CardTitle className="text-2xl font-bold">{eventInfo.name}</CardTitle>
                        <CardDescription>This event is protected. Please enter the secret code to continue.</CardDescription>
                    </CardHeader>
                    <form onSubmit={handleVerifyEmailCode}>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="code">Enter Secret Code</Label>
                                <Input
                                    id="code"
                                    type="text"
                                    placeholder="••••••"
                                    className="h-14 text-center text-2xl font-black tracking-widest bg-muted/50 transition-all border-border focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
                                    value={secretCode}
                                    onChange={(e) => setSecretCode(e.target.value)}
                                    autoFocus
                                />
                            </div>
                        </CardContent>
                        <CardFooter>
                            <Button className="w-full mt-4 h-12 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl" disabled={loading || !secretCode}>
                                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Access Event
                            </Button>
                        </CardFooter>
                    </form>
                </Card>
            </div>
        );
    }

    if (success) {
        return (
            <div className="min-h-screen bg-background flex items-center justify-center p-4">
                <Card className="w-full max-w-md border-none shadow-xl bg-card/80 backdrop-blur-md">
                    <CardHeader className="text-center">
                        <div className="mx-auto w-16 h-16 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mb-4">
                            <CheckCircle2 className="w-10 h-10 text-green-600 dark:text-green-400" />
                        </div>
                        <CardTitle className="text-2xl font-bold text-foreground">
                            {matchCount > 0 ? `Found ${matchCount} photos!` : "Your gallery is ready!"}
                        </CardTitle>
                        <CardDescription className="text-muted-foreground mt-2">
                            We&apos;ve matched your photos and created a personal gallery.
                        </CardDescription>
                    </CardHeader>
                    <CardFooter className="flex flex-col gap-3">
                        <Button className="w-full h-12 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl" asChild>
                            <a href={`/event/${slug}/guest/${requestId}`} rel="noopener noreferrer">
                                Open My Personal Gallery
                            </a>
                        </Button>
                        <p className="text-xs text-center text-muted-foreground">
                            It might take a minute for all photos to appear.
                        </p>
                    </CardFooter>
                </Card>
            </div>
        );
    }

    if (!showUploadForm && savedGuests.length > 0) {
        return (
            <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4 bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-blue-100/20 via-background to-indigo-100/20 text-foreground transition-colors duration-300">
                <Card className="w-full max-w-2xl border border-border shadow-2xl bg-card/90 backdrop-blur-lg">
                    <CardHeader className="text-center pb-2">
                        <CardTitle className="text-3xl font-bold tracking-tight text-foreground">Welcome Back!</CardTitle>
                        <CardDescription className="text-base text-muted-foreground mt-2">
                            Select your face to view your personal gallery at <span className="font-semibold text-indigo-600">{eventInfo.name}</span>
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="pt-6 pb-8">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            {savedGuests.map((guest) => (
                                <div
                                    key={guest.id}
                                    className="group relative flex items-center gap-4 p-4 rounded-2xl border border-border bg-background/50 hover:bg-indigo-50/50 hover:border-indigo-200 transition-all duration-300 shadow-sm hover:shadow-md"
                                >
                                    <a
                                        href={`/event/${slug}/guest/${guest.id}`}
                                        className="flex flex-1 items-center gap-4 min-w-0"
                                    >
                                        <div className="relative w-16 h-16 rounded-full overflow-hidden border-2 border-muted group-hover:border-indigo-400 transition-colors bg-muted flex-shrink-0">
                                            {/* eslint-disable-next-line @next/next/no-img-element */}
                                            <img
                                                src={`${process.env.NEXT_PUBLIC_API_URL}/guests/selfie/${guest.id}`}
                                                alt={guest.name}
                                                className="w-full h-full object-cover"
                                                onError={(e) => {
                                                    const target = e.target as HTMLImageElement;
                                                    target.style.display = 'none';
                                                    target.parentElement?.classList.add('flex', 'items-center', 'justify-center');
                                                    const icon = document.createElement('div');
                                                    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-8 h-8 text-muted-foreground"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
                                                    target.parentElement?.appendChild(icon.firstChild!);
                                                }}
                                            />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <p className="font-bold text-foreground truncate group-hover:text-indigo-600 transition-colors uppercase text-sm tracking-wide">{guest.name}</p>
                                            {guest.email && <p className="text-xs text-muted-foreground truncate">{guest.email}</p>}
                                        </div>
                                        <ArrowRight className="w-5 h-5 text-muted-foreground group-hover:text-indigo-600 group-hover:translate-x-1 transition-all" />
                                    </a>
                                    <button
                                        onClick={(e) => {
                                            e.preventDefault();
                                            e.stopPropagation();
                                            removeSavedGuest(guest.id);
                                        }}
                                        className="p-2 text-red-500 hover:bg-red-50 rounded-full transition-colors opacity-100 group-hover:opacity-100"
                                        title="Remove from this device"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            ))}

                            <button
                                onClick={() => setShowUploadForm(true)}
                                className="group flex items-center gap-4 p-4 rounded-2xl border-2 border-dashed border-border bg-transparent hover:bg-muted/30 hover:border-indigo-300 transition-all duration-300"
                            >
                                <div className="w-16 h-16 rounded-full border-2 border-dashed border-muted flex items-center justify-center group-hover:bg-indigo-50 group-hover:border-indigo-400 transition-colors">
                                    <UserPlus className="w-6 h-6 text-muted-foreground group-hover:text-indigo-600" />
                                </div>
                                <div className="text-left">
                                    <p className="font-bold text-foreground group-hover:text-indigo-600 transition-colors">Add New Face</p>
                                    <p className="text-xs text-muted-foreground">Join with a different selfie</p>
                                </div>
                            </button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4 bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-blue-100/20 via-background to-indigo-100/20 text-foreground transition-colors duration-300">
            {savedGuests.length > 0 && (
                <Button
                    variant="ghost"
                    onClick={() => setShowUploadForm(false)}
                    className="mb-4 text-muted-foreground hover:text-indigo-600"
                >
                    <ArrowRight className="w-4 h-4 mr-2 rotate-180" />
                    Back to my faces
                </Button>
            )}
            <Card className="w-full max-w-lg border border-border shadow-2xl bg-card/90 backdrop-blur-lg">
                <CardHeader className="space-y-1">
                    <CardTitle className="text-3xl font-bold tracking-tight text-foreground">Find your photos</CardTitle>
                    <CardDescription className="text-muted-foreground">
                        {isCameraActive ? "Take a live selfie to find your event photos." : "Upload a selfie to find your event photos."}
                    </CardDescription>
                </CardHeader>
                <form onSubmit={handleSubmit}>
                    <CardContent className="space-y-6">
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <Label className="text-base font-semibold text-foreground">Your selfie</Label>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setIsCameraActive(!isCameraActive);
                                        retake();
                                    }}
                                    className="text-indigo-600 dark:text-indigo-400 text-sm font-medium hover:underline flex items-center gap-1"
                                >
                                    {isCameraActive ? <Upload className="w-3 h-3" /> : <Camera className="w-3 h-3" />}
                                    {isCameraActive ? "Switch to Upload" : "Switch to Live Camera"}
                                </button>
                            </div>

                            {isCameraActive ? (
                                <div className="relative aspect-square w-full max-w-sm mx-auto overflow-hidden rounded-2xl bg-muted border-2 border-border shadow-inner">
                                    {capturedImage ? (
                                        <div className="relative w-full h-full animate-in fade-in zoom-in-95 duration-300">
                                            {/* eslint-disable-next-line @next/next/no-img-element */}
                                            <img src={capturedImage} alt="Selfie" className="w-full h-full object-cover scale-x-[-1]" />
                                            <button
                                                type="button"
                                                onClick={retake}
                                                className="absolute bottom-4 right-4 bg-white/90 backdrop-blur-sm text-slate-900 px-4 py-2 rounded-full text-sm font-bold shadow-lg hover:bg-white flex items-center gap-2"
                                            >
                                                <RefreshCw className="w-4 h-4" />
                                                Retake
                                            </button>
                                        </div>
                                    ) : (
                                        <>
                                            <Webcam
                                                audio={false}
                                                ref={webcamRef}
                                                screenshotFormat="image/jpeg"
                                                videoConstraints={videoConstraints}
                                                className="w-full h-full object-cover scale-x-[-1]"
                                                onUserMediaError={(err) => {
                                                    console.error("Camera Error:", err);
                                                    toast.error("Could not access camera. Please check permissions or use the upload option.");
                                                    setIsCameraActive(false);
                                                }}
                                            />
                                            <div className="absolute inset-0 border-4 border-white/20 rounded-full scale-75 border-dashed pointer-events-none" />
                                            <button
                                                type="button"
                                                onClick={capture}
                                                className="absolute bottom-6 left-1/2 -translate-x-1/2 bg-white text-indigo-600 p-4 rounded-full shadow-2xl hover:scale-105 active:scale-95 transition-all group"
                                            >
                                                <div className="w-12 h-12 rounded-full border-4 border-indigo-600 flex items-center justify-center group-hover:bg-indigo-50">
                                                    <Camera className="w-6 h-6" />
                                                </div>
                                            </button>
                                        </>
                                    )}
                                </div>
                            ) : (
                                <div className="border-2 border-dashed border-border rounded-2xl p-8 text-center hover:border-indigo-400 transition-all cursor-pointer group bg-muted/30"
                                    onClick={() => document.getElementById('selfie-input')?.click()}>
                                    <input
                                        type="file"
                                        id="selfie-input"
                                        className="hidden"
                                        accept="image/*"
                                        capture="user"
                                        onChange={(e) => {
                                            const f = e.target.files?.[0] || null;
                                            setFile(f);
                                            setFileName(f?.name || null);
                                        }}
                                    />
                                    {fileName ? (
                                        <div className="flex flex-col items-center gap-3 animate-in fade-in duration-300">
                                            <div className="p-4 bg-indigo-100 dark:bg-indigo-900/30 rounded-full">
                                                <CheckCircle2 className="w-8 h-8 text-indigo-600 dark:text-indigo-400" />
                                            </div>
                                            <div>
                                                <p className="font-semibold text-foreground">{fileName}</p>
                                                <button type="button" className="text-xs text-red-500 font-bold mt-1 flex items-center justify-center gap-1 mx-auto" onClick={(e) => { e.stopPropagation(); retake(); }}>
                                                    <XCircle className="w-3 h-3" /> Remove
                                                </button>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="space-y-4 py-4">
                                            <div className="mx-auto w-16 h-16 bg-card rounded-full shadow-md flex items-center justify-center group-hover:scale-110 transition-transform">
                                                <Upload className="w-8 h-8 text-muted-foreground group-hover:text-indigo-600" />
                                            </div>
                                            <div className="space-y-1">
                                                <p className="text-sm font-semibold text-foreground">Click to upload or drag & drop</p>
                                                <p className="text-xs text-muted-foreground">PNG, JPG or WEBP (max. 10MB)</p>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </CardContent>
                    <CardFooter className="pt-2 pb-8">
                        <Button className="w-full h-14 bg-indigo-600 hover:bg-indigo-700 text-white text-lg font-bold rounded-2xl shadow-xl shadow-indigo-200 dark:shadow-none transition-all border-none disabled:bg-muted disabled:text-muted-foreground"
                            disabled={loading || !file}>
                            {loading ? (
                                <>
                                    <Loader2 className="mr-3 h-5 w-5 animate-spin" />
                                    Finding your memories...
                                </>
                            ) : (
                                "Show Me My Photos"
                            )}
                        </Button>
                    </CardFooter>
                </form>
            </Card>

            <p className="mt-8 text-sm text-muted-foreground text-center max-w-xs leading-tight">
                Privacy Protected: Your selfie is used only for matching and is deleted immediately.
            </p>
        </div>
    );
}
