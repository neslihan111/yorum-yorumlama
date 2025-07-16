from flask import Flask, render_template, request, jsonify
from googleapiclient.discovery import build
from pymongo import MongoClient
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import os
from dotenv import load_dotenv
import time
import traceback

app = Flask(__name__)

# Load environment variables
load_dotenv()

# MongoDB bağlantısını opsiyonel yap
MONGODB_ENABLED = False  # MongoDB'yi varsayılan olarak devre dışı bırak
try:
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000)
    # Bağlantıyı test et
    client.server_info()
    db = client['youtube_comments']
    comments_collection = db['comments']
    MONGODB_ENABLED = True
    print("MongoDB bağlantısı başarılı!")
except Exception as e:
    print(f"MongoDB bağlantısı kurulamadı: {e}")
    print("Uygulama MongoDB olmadan çalışacak.")
    client = None
    db = None
    comments_collection = None

# YouTube API setup
API_KEY = "AIzaSyBkZAQ7j2YsYBu91TCVKl01W6T89rPpxcQ"  # Buraya kendi API anahtarınızı yazın
youtube = build('youtube', 'v3', developerKey=API_KEY)

# Initialize VADER sentiment analyzer
sentiment_analyzer = SentimentIntensityAnalyzer()

def get_video_id(url):
    """Extract video ID from YouTube URL"""
    try:
        if 'youtu.be' in url:
            return url.split('/')[-1]
        elif 'youtube.com' in url:
            return url.split('v=')[1].split('&')[0]
        return None
    except Exception as e:
        print(f"Video ID çıkarma hatası: {e}")
        return None

def get_video_comments(video_id, max_results=25):
    """Fetch comments from YouTube video"""
    comments = []
    try:
        # İlk sayfa yorumları
        request = youtube.commentThreads().list(
            part='snippet',
            videoId=video_id,
            maxResults=100,  # Her sayfada maksimum 100 yorum
            textFormat='plainText'
        )
        response = request.execute()
        
        # İlk sayfa yorumlarını ekle
        for item in response['items']:
            comment = item['snippet']['topLevelComment']['snippet']
            comments.append({
                'author': comment['authorDisplayName'],
                'text': comment['textDisplay'],
                'likes': comment['likeCount'],
                'published_at': comment['publishedAt']
            })
        
        # Eğer max_results 0 ise (tüm yorumlar) ve daha fazla yorum varsa, diğer sayfaları da getir
        if max_results == 0 and 'nextPageToken' in response:
            while 'nextPageToken' in response and len(comments) < 1000:  # YouTube API limiti
                request = youtube.commentThreads().list(
                    part='snippet',
                    videoId=video_id,
                    maxResults=100,
                    textFormat='plainText',
                    pageToken=response['nextPageToken']
                )
                response = request.execute()
                
                for item in response['items']:
                    comment = item['snippet']['topLevelComment']['snippet']
                    comments.append({
                        'author': comment['authorDisplayName'],
                        'text': comment['textDisplay'],
                        'likes': comment['likeCount'],
                        'published_at': comment['publishedAt']
                    })
        elif max_results > 0:
            # Belirli sayıda yorum isteniyorsa, listeyi kısalt
            comments = comments[:max_results]
            
    except Exception as e:
        print(f"Error fetching comments: {e}")
        print(traceback.format_exc())
    return comments

def analyze_sentiment(text):
    """Analyze sentiment using VADER"""
    try:
        # VADER sentiment analizi
        scores = sentiment_analyzer.polarity_scores(text)
        
        # Compound score -1 ile 1 arasında
        compound = scores['compound']
        
        # Duygu kategorisi belirleme
        if compound >= 0.05:
            return 'positive'
        elif compound <= -0.05:
            return 'negative'
        else:
            return 'neutral'
            
    except Exception as e:
        print(f"Duygu analizi hatası: {e}")
        return 'neutral'

def filter_comments_by_keyword(comments, keyword):
    """Filter comments based on keyword"""
    if not keyword:
        return comments
    
    keyword = keyword.lower()
    return [comment for comment in comments if keyword in comment['text'].lower()]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        start_time = time.time()
        url = request.form['url']
        comment_count = int(request.form.get('comment_count', 25))
        keyword = request.form.get('keyword', '').strip()  # Get keyword from form
        
        video_id = get_video_id(url)
        if not video_id:
            return jsonify({'error': 'Geçersiz YouTube URL\'si'})
        
        # Get video title
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if not video_response['items']:
            return jsonify({'error': 'Video bulunamadı'})
            
        video_title = video_response['items'][0]['snippet']['title']
        
        # Get comments
        comments = get_video_comments(video_id, max_results=comment_count)
        
        # Filter comments by keyword if provided
        if keyword:
            comments = filter_comments_by_keyword(comments, keyword)
        
        # Analyze comments
        stats = {'positive': 0, 'neutral': 0, 'negative': 0}
        analyzed_comments = []
        
        for comment in comments:
            sentiment = analyze_sentiment(comment['text'])
            stats[sentiment] += 1
            comment['sentiment'] = sentiment
            analyzed_comments.append(comment)
            
            # MongoDB'ye kaydet (eğer bağlantı varsa)
            if MONGODB_ENABLED and comments_collection:
                try:
                    comments_collection.insert_one({
                        'video_id': video_id,
                        'video_title': video_title,
                        'comment': comment
                    })
                except Exception as e:
                    print(f"MongoDB kayıt hatası: {e}")
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        return jsonify({
            'video_id': video_id,
            'video_title': video_title,
            'stats': stats,
            'comments': analyzed_comments,
            'mongodb_enabled': MONGODB_ENABLED,
            'processing_time': f"{processing_time:.2f} saniye",
            'total_comments': len(analyzed_comments),
            'keyword': keyword if keyword else None
        })
        
    except Exception as e:
        print(f"Genel hata: {e}")
        print(traceback.format_exc())
        return jsonify({'error': 'Bir hata oluştu. Lütfen tekrar deneyin.'})
    
@app.route('/contact', methods=['POST'])
def contact():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    message = data.get('message')

    # Burada mesajı kaydedebilir, e-posta gönderebilir veya başka bir işlem yapabilirsin.
    # Şimdilik sadece başarılı yanıt dönelim:
    return jsonify({'success': True}), 200    
    

if __name__ == '__main__':
    app.run(debug=True)