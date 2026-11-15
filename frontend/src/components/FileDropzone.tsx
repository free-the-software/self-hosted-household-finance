// Copyright (C) 2026 free-the-software
// SPDX-License-Identifier: AGPL-3.0-or-later
// This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
import { useState, useRef } from 'react';
import { ArrowUpTrayIcon, DocumentIcon, ArrowPathIcon } from '@heroicons/react/24/outline';
import { apiFetch } from '../api';

interface FileDropzoneProps {
    onUploadComplete?: () => void;
}

const FileDropzone = ({ onUploadComplete }: FileDropzoneProps) => {
    const [dragActive, setDragActive] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    // Handle Drag Events
    const handleDrag = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "dragenter" || e.type === "dragover") {
            setDragActive(true);
        } else if (e.type === "dragleave") {
            setDragActive(false);
        }
    };

    // Handle Drop
    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFiles(e.dataTransfer.files[0]);
        }
    };

    // Handle Manual Click Selection
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        e.preventDefault();
        if (e.target.files && e.target.files[0]) {
            handleFiles(e.target.files[0]);
        }
    };

    const handleFiles = async (file: File) => {
        setUploading(true);
        setMessage(null);

        const formData = new FormData();
        formData.append('file', file);

        try {
            // Using relative URL which Vite proxies to Django
            const response = await apiFetch('/api/upload/', {
                method: 'POST',
                body: formData,
            });

            if (response.ok) {
                setMessage({ type: 'success', text: `Uploaded ${file.name} successfully!` });
                if (onUploadComplete) onUploadComplete();
                
                // Clear the message after 3 seconds
                setTimeout(() => setMessage(null), 3000);
            } else {
                // The server says why: wrong type, too large, already waiting...
                const data = await response.json().catch(() => null);
                setMessage({ type: 'error', text: data?.error || `Upload failed (error ${response.status}).` });
            }
        } catch (error) {
            setMessage({ type: 'error', text: 'Network error uploading file.' });
        } finally {
            setUploading(false);
            // Reset the input so you can select the same file again if needed
            if (inputRef.current) inputRef.current.value = '';
        }
    };

    const onButtonClick = () => {
        inputRef.current?.click();
    };

    return (
        <div className="w-full mb-6">
            <div 
                className={`relative border-2 border-dashed rounded-xl p-2 text-center transition-all duration-200 ease-in-out ${
                    dragActive 
                    ? "border-blue-500 bg-blue-500/10 scale-[1.01]" 
                    : "border-gray-700 bg-gray-800/50 hover:border-gray-600 hover:bg-gray-800"
                }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
            >
                <input 
                    ref={inputRef}
                    type="file" 
                    className="hidden" 
                    onChange={handleChange}
                    accept=".qfx,.ofx,.csv,.jpg,.jpeg,.png,.heic" 
                />

                <div className="flex flex-col items-center justify-center space-y-3 pointer-events-none">
                    <div className={`p-3 rounded-full ${dragActive ? 'bg-blue-500/20 text-blue-400' : 'bg-gray-700 text-gray-400'}`}>
                        {uploading ? (
                            // CORRECTED LINE: Using the imported HeroIcon
                            <ArrowPathIcon className="w-8 h-8 animate-spin" />
                        ) : (
                            <ArrowUpTrayIcon className="w-6 h-6" />
                        )}
                    </div>
                    
                    <div className="text-gray-300 font-medium">
                        {uploading ? (
                            <span>Uploading to Watcher...</span>
                        ) : (
                            <span>
                                <span className="text-blue-400 cursor-pointer hover:underline pointer-events-auto" onClick={onButtonClick}>
                                    Click to upload
                                </span> or drag and drop
                            </span>
                        )}
                    </div>
                    
                    <p className="text-xs text-gray-500">
                        .qfx, .jpg, or .jpeg (Receipts)
                    </p>
                </div>

                {/* Status Message Overlay */}
                {message && (
                    <div className="absolute inset-0 flex items-center justify-center rounded-xl bg-gray-900/90 backdrop-blur-sm animate-in fade-in">
                        <div className={`text-sm font-bold flex items-center ${message.type === 'success' ? 'text-green-400' : 'text-red-400'}`}>
                            {message.type === 'success' ? (
                                <DocumentIcon className="w-5 h-5 mr-2" />
                            ) : null}
                            {message.text}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default FileDropzone;