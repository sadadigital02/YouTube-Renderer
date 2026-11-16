import os
import sys
import json
import time
import subprocess
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def log_status(step, message):
    print(f"[{step}] {message}", flush=True)

def get_drive_service():
    client_id = os.environ.get("GDRIVE_CLIENT_ID")
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET")
    refresh_token = os.environ.get("GDRIVE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("Missing Google Drive OAuth credentials in environment variables.")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

def extract_audio(youtube_url, output_audio):
    log_status("AUDIO_EXTRACT", f"Extracting audio from: {youtube_url}")
    cmd = [
        "yt-dlp",
        "--remote-components", "ejs:github",
        "-f", "bestaudio/best",
        "--no-playlist",
        "-o", output_audio,
    ]
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        cmd.extend(["--cookies", "cookies.txt"])

    cmd.append(youtube_url)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")
    log_status("AUDIO_EXTRACT", "Audio extracted successfully.")

def render_video(image_path, audio_path, output_video, video_bitrate, audio_bitrate, preset):
    log_status("RENDER", f"Starting FFmpeg render (Video: {video_bitrate}, Audio: {audio_bitrate}, Preset: {preset})")
    start_time = time.time()

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Uploaded image not found at path: {image_path}")

    vf_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"

    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-framerate", "1",
        "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-preset", preset,
        "-tune", "stillimage",
        "-b:v", video_bitrate,
        "-maxrate", video_bitrate,
        "-bufsize", f"{int(video_bitrate.replace('k', '')) * 2}k",
        "-r", "30",
        "-vf", vf_filter,
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        output_video
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    elapsed = round(time.time() - start_time, 2)
    file_size_mb = round(os.path.getsize(output_video) / (1024 * 1024), 2)
    log_status("RENDER", f"Render completed in {elapsed}s. File size: {file_size_mb} MB.")
    return elapsed, file_size_mb

def upload_video_to_drive(service, file_path, file_name, folder_id=None):
    log_status("DRIVE_UPLOAD", f"Uploading {file_name} to Google Drive...")
    file_metadata = {"name": file_name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink, webContentLink"
    ).execute()

    file_id = file.get("id")
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"}
    ).execute()

    log_status("DRIVE_UPLOAD", f"Upload successful! File ID: {file_id}")
    return file.get("webViewLink"), file.get("webContentLink"), file_id

def main():
    job_id = os.environ.get("JOB_ID", "test_job")
    youtube_url = os.environ.get("YOUTUBE_URL")
    image_path = os.environ.get("IMAGE_PATH")
    video_bitrate = os.environ.get("VIDEO_BITRATE", "3000k")
    audio_bitrate = os.environ.get("AUDIO_BITRATE", "256k")
    preset = os.environ.get("FFMPEG_PRESET", "veryfast")
    output_folder_id = os.environ.get("GDRIVE_FOLDER_ID", "")

    if not youtube_url or not image_path:
        raise ValueError("YOUTUBE_URL and IMAGE_PATH are required.")

    audio_path = "input_audio.m4a"
    output_video = f"Render_{job_id}.mp4"

    # 1. Extract Audio
    extract_audio(youtube_url, audio_path)

    # 2. Render Video using directly uploaded image
    render_time, size_mb = render_video(
        image_path, audio_path, output_video, video_bitrate, audio_bitrate, preset
    )

    # 3. Upload final MP4 to Google Drive
    service = get_drive_service()
    view_link, download_link, out_file_id = upload_video_to_drive(
        service, output_video, output_video, output_folder_id if output_folder_id else None
    )

    # Save job result
    result_data = {
        "status": "SUCCESS",
        "job_id": job_id,
        "render_time_sec": render_time,
        "file_size_mb": size_mb,
        "view_link": view_link,
        "download_link": download_link,
        "drive_file_id": out_file_id
    }
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    log_status("DONE", json.dumps(result_data, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_data = {"status": "FAILED", "error": str(e)}
        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False, indent=2)
        log_status("ERROR", str(e))
        sys.exit(1)
