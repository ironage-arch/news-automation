import feedparser
import requests
import json
import re
import openai
import os
import os.path
import datetime
import smtplib
import getpass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from email.header import Header
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import difflib

# ==============================================================================
# --- 1. 사용자 설정 (GitHub Actions Secrets에서 자동으로 불러옵니다) ---
# ==============================================================================

# Google Alerts RSS 주소
GOOGLE_ALERTS_RSS_URLS = [
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487599294", #Satellite Communications
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/7282625974461397688", #위성통신
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487600193", #Non terrestrial Networks
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487600258", #3GPP
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490706746", #6G
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/13972650129806487379", #저궤도
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/5231113795348014351", #FCC 47 CFR PArt 25
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490708240", #low Earth orbit
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/12348804382892789873", #주파수
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490708655", #AI-RAN
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/270492137594840372", #AI-RAN
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356182211", #AI network
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356181274", #ITU-R
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/18373922797329225191", #ISAC
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356184244", #IMT-2030
]

# Naver 검색 키워드
NAVER_QUERIES = [
    "위성통신", "satellite communication",
    "저궤도", "LEO",
    "ICT 표준", "ICT standardization",
    "주파수 정책", "spectrum policy",
    "3GPP", "ITU", "FCC", "ofcom"
]

# GitHub Secrets를 통해 환경 변수에서 API 키와 설정값 불러오기
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")
RECEIVER_EMAIL = [email.strip() for email in os.environ.get("RECEIVER_EMAIL", "").split(',') if email.strip()]

# Google API 설정
SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

# ==============================================================================
# --- 헬퍼 함수: URL 최종 목적지 추적 (고도화) ---
# ==============================================================================
def get_final_url(url):
    """리디렉션을 추적하여 최종 URL을 찾아내는 고도화된 함수"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
        response.raise_for_status()
        return response.url
    except requests.RequestException:
        try:
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            response.raise_for_status()
            return response.url
        except requests.RequestException as e:
            return url

# ==============================================================================
# --- 2. 뉴스 수집 함수 (단순 수집 및 링크 기반 중복 제거) ---
# ==============================================================================
def get_news_data():
    """여러 RSS 피드와 키워드에서 뉴스를 수집하고 1차 중복을 제거하는 함수"""
    news_list = []
    
    print("\n- Google Alerts에서 뉴스를 수집합니다...")
    for url in GOOGLE_ALERTS_RSS_URLS:
        if not url.strip(): continue
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                raw_link = entry.link.split("&url=")[1] if "&url=" in entry.link else entry.link
                final_link = get_final_url(raw_link)
                published_date = datetime.datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d') if hasattr(entry, 'published_parsed') else "날짜 정보 없음"
                news_list.append({"title": entry.title, "link": final_link, "published": published_date, "source": "Google Alerts"})
        except Exception as e:
            print(f"  (경고) 구글 알리미 RSS 불러오기 실패 ({url}): {e}")

    print("- Naver News에서 뉴스를 수집합니다...")
    for query in NAVER_QUERIES:
        if not query.strip(): continue
        try:
            naver_url = "https://openapi.naver.com/v1/search/news.json"
            headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
            params = {"query": query, "display": 10, "sort": "date"}
            response = requests.get(naver_url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            for item in data.get("items", []):
                clean_title = re.sub('<[^>]*>', '', item["title"])
                published_date = datetime.datetime.strptime(item['pubDate'], '%a, d %b %Y %H:%M:%S +0900').strftime('%Y-%m-%d')
                raw_link = item.get("originallink", item["link"])
                news_list.append({"title": clean_title, "link": raw_link, "published": published_date, "source": "Naver News"})
        except Exception as e:
            print(f"  (경고) 네이버 뉴스 API 불러오기 실패 ({query}): {e}")

    # 1차 중복 제거: URL 기준
    news_list.sort(key=lambda x: x['published'], reverse=True)
    unique_news_items = []
    seen_links = set()

    for item in news_list:
        # HTML 태그 제거 및 제목 정제
        item['title'] = re.sub('<[^>]*>', '', item['title'])
        normalized_link = re.sub(r'^https?:\/\/(www\.)?', '', item['link']).rstrip('/')
        if normalized_link not in seen_links:
            unique_news_items.append(item)
            seen_links.add(normalized_link)
            
    return unique_news_items

# ==============================================================================
# --- 3. AI 뉴스 선별 함수 (✨ 중복 제거 로직 강화) ---
# ==============================================================================
def filter_news_by_ai(news_items):
    """AI를 사용해 의미적으로 중복되는 뉴스를 제거하고, 정책 입안자에게 중요한 뉴스를 선별하는 함수"""
    print("\n[🚀 작업 중] AI가 의미 기반으로 뉴스를 분석하여 중복을 제거하고 핵심 뉴스를 선별합니다...")
    if not OPENAI_API_KEY or not OPENAI_API_KEY.startswith("sk-"):
        print("  (경고) OpenAI API 키가 없어 뉴스 선별을 건너뛰고 최신 뉴스 20개를 분석합니다.")
        return news_items[:20]

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    formatted_news_list = ""
    for i, item in enumerate(news_items):
        formatted_news_list += f"[{i}] {item['title']}\n"

    prompt = f"""
    당신은 ICT 표준 정책 최고 전문가의 수석 보좌관입니다.
    당신의 임무는 아래 뉴스 목록에서 의미적으로 중복되는 기사를 완벽하게 제거한 뒤, '표준 정책 입안자'의 관점에서 가장 중요한 뉴스 20개를 선별하는 것입니다.

    [작업 절차]
    1. **의미 기반 중복 제거 (가장 중요)**: 아래 뉴스 목록을 주의 깊게 읽고, 제목의 표현이 조금 다르더라도 사실상 '동일한 사건'이나 '동일한 주제'를 다루는 기사들을 모두 찾아내세요. 각 중복 그룹에서 가장 대표적인 기사 **하나만** 남기고 나머지는 모두 제거합니다.
       - 예시 1: "[전자파학회] 이재성 학회장 '6G 위성...'" 과 "[전자파학회] K-전파, 6G 위성·우주국방..." 은 동일한 행사 기사이므로 하나만 선택합니다.
       - 예시 2: "정부, '5G특화망2.0' 추진..." 과 "정부, 전파진흥계획 구체화..." 가 동일한 정책 발표라면 하나만 선택합니다.
    2. **최종 선별**: 중복이 완벽히 제거된 뉴스 목록에서, 아래 [선별 최우선 기준]에 따라 정책적 중요도가 가장 높은 뉴스 20개를 최종적으로 선별합니다.

    [선별 최우선 기준]
    - **해외 주요국 정책/규제**: 미국(FCC), 유럽(ETSI), 영국(Ofcom) 등의 법안, 규제, 정책 변화
    - **국제 표준화 동향**: 3GPP, ITU, IEEE 등의 의사결정, 차세대 기술(6G, AI, 위성통신) 표준화 방향
    - **국내 정부 계획 및 발표**: 과기정통부, 방통위 등의 핵심 정책, 법·제도 개정, 국가 R&D 전략
    - **산업계 핵심 동향**: ICT 산업 판도에 영향을 미치는 국내외 기업의 기술 개발 및 사업 전략
    - **TTA 관련 보도**: TTA 공식 보도자료, 주요 인사 발언, 인터뷰 등

    [뉴스 목록]
    {formatted_news_list}

    [요청]
    위 절차와 기준에 따라 최종적으로 선별된 뉴스의 번호(인덱스) 20개만 쉼표(,)로 구분하여 응답해 주십시오.
    다른 설명이나 텍스트는 절대 포함하지 말고, 번호만 응답해야 합니다.
    예시: 3, 8, 12, 15, 21, 23, 25, 30, 31, 33, 40, 41, 42, 45, 50, 51, 52, 53, 54, 55
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 ICT 표준 정책 전문가의 유능한 보좌관입니다. 주어진 뉴스 목록에서 의미적으로 중복되는 것을 완벽히 제거하고, 정책적 중요도가 가장 높은 20개를 골라 번호만 응답합니다."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
        )
        selected_indices_str = response.choices[0].message.content
        print(f"  > AI가 선별한 최종 뉴스 인덱스: {selected_indices_str}")
        
        # 정규식을 사용하여 응답에서 숫자만 추출 (안정성 강화)
        indices = re.findall(r'\d+', selected_indices_str)
        selected_indices = [int(i) for i in indices]
        
        filtered_news = [news_items[i] for i in selected_indices if i < len(news_items)]
        if not filtered_news: raise ValueError("AI가 유효한 인덱스를 반환하지 않았습니다.")
        return filtered_news
    except Exception as e:
        print(f"  (경고) AI 뉴스 선별 실패: {e}. 최신 뉴스 20개로 대체합니다.")
        return news_items[:20]

# ==============================================================================
# --- 4. AI 심층 분석 함수 (보고서 형식 구체화) ---
# ==============================================================================
def analyze_news_with_ai(news_item):
    """AI에게 뉴스를 보내 구체화된 전문가 보고서 형식으로 심층 분석을 요청하는 함수"""
    if not OPENAI_API_KEY or not OPENAI_API_KEY.startswith("sk-"):
        return "OpenAI API 키가 설정되지 않아 분석을 건너뜁니다."
        
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    당신은 20년 경력의 ICT 표준 및 정책 분야 최고 전문가입니다.
    아래 뉴스 기사를 분석하여, 다음 4가지 항목으로 구성된 전문가 보고서를 작성해 주십시오.
    모든 내용은 중학생도 이해할 수 있도록 명확하고 쉬운 언어를 사용해야 합니다.

    [뉴스 정보]
    - 뉴스 제목: {news_item['title']}
    - 원문 링크: {news_item['link']}

    [보고서 작성 형식]
    - **주요 내용:** (기사의 핵심 사실과 정보를 한 문장으로 요약한 뒤, 3개의 글머리 기호(bullet point)로 상세 정리)
      - 
      - 
      - 
    - **시사점 및 전망:** (이 뉴스가 ICT 표준, 규제, 시장에 미치는 영향과 향후 전망을 한 문장으로 요약한 뒤, 3개의 글머리 기호(bullet point)로 상세 분석)
      - 
      - 
      - 
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 ICT 표준 정책 분석 최고 전문가입니다. 모든 답변은 '주요 내용', '시사점 및 전망' 각각에 대해 한 문장 요약과 3개의 글머리 기호 형식으로 작성해 주세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5, max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  (경고) AI 심층 분석 실패 ({news_item['title']}): {e}")
        return "AI 심층 분석에 실패했습니다."

# ==============================================================================
# --- 5. 구글 문서 생성 함수 (API 오류 수정) ---
# ==============================================================================
def get_google_services():
    """Google Docs와 Drive API 서비스를 인증하고 생성하는 함수"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f" (정보) 토큰 갱신 실패: {e}. 'token.json'을 삭제하고 다시 인증을 시도합니다.")
                if os.path.exists('token.json'):
                    os.remove('token.json')
                creds = None
    
    if not creds:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return docs_service, drive_service

def generate_google_doc_report(analyzed_data):
    try:
        docs_service, drive_service = get_google_services()
    except FileNotFoundError:
        print("  (오류) 'credentials.json' 파일을 찾을 수 없습니다. 구글 인증 설정을 확인하세요.")
        return None, None
    except Exception as e:
        print(f"  (오류) 구글 서비스 연결에 실패했습니다: {e}")
        return None, None
        
    current_date = datetime.date.today().strftime('%Y년 %m월 %d일')
    document_title = f"ICT 주요 기술 동향 보고서 ({current_date})"
    
    try:
        # 1. 문서 생성
        document = docs_service.documents().create(body={'title': document_title}).execute()
        document_id = document.get('documentId')
        document_url = f"https://docs.google.com/document/d/{document_id}/edit"
        print(f"  > 새 문서가 생성되었습니다: {document_url}")

        # 2. 스타일링된 내용 추가
        requests = []
        index = 1

        # --- 문서 제목 스타일링 ---
        title_text = f"{document_title}\n"
        requests.append({'insertText': {'location': {'index': index}, 'text': title_text}})
        requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(title_text)}, 'paragraphStyle': {'alignment': 'CENTER'}, 'fields': 'alignment'}})
        requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(title_text) - 1}, 'textStyle': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'bold': True}, 'fields': 'fontSize,bold'}})
        index += len(title_text)
        
        # --- AI 분석 고지 문구 ---
        disclaimer_text = "※ 본 보고서의 내용은 AI가 생성한 분석으로, 개인적인 의견을 포함하지 않습니다.\n\n"
        requests.append({'insertText': {'location': {'index': index}, 'text': disclaimer_text}})
        requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer_text)}, 'paragraphStyle': {'alignment': 'CENTER'}, 'fields': 'alignment'}})
        requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer_text) - 2}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'italic': True, 'foregroundColor': {'color': {'rgbColor': {'red': 0.5, 'green': 0.5, 'blue': 0.5}}}}, 'fields': 'fontSize,italic,foregroundColor'}})
        index += len(disclaimer_text)


        # --- 각 뉴스 아이템 스타일링 ---
        for i, data in enumerate(analyzed_data):
            # 뉴스 제목
            news_title = f"[{i+1}] {data['title']}\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': news_title}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(news_title)}, 'textStyle': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'bold': True}, 'fields': 'fontSize,bold'}})
            index += len(news_title)
            
            # 메타데이터 (출처, 발행일, 링크)
            meta_text = f"출처: {data['source']} | 발행일: {data['published']}\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': meta_text}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(meta_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'foregroundColor': {'color': {'rgbColor': {'red': 0.5, 'green': 0.5, 'blue': 0.5}}}}, 'fields': 'fontSize,foregroundColor'}})
            index += len(meta_text)
            
            link_text = f"원본 링크: {data['link']}\n\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': link_text}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(link_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'link': {'url': data['link']}}, 'fields': 'fontSize,link'}})
            index += len(link_text)

            # 분석 내용 파싱 (정규식 수정)
            analysis_text = data.get('analysis_result', '')
            main_content_match = re.search(r'\*\*(주요 내용):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL)
            implications_match = re.search(r'\*\*(시사점 및 전망):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL)
            main_content = main_content_match.group(2).strip() if main_content_match else "주요내용 정보 없음"
            implications = implications_match.group(2).strip() if implications_match else "시사점 정보 없음"

            # 주요 내용 섹션 (타이틀 수정)
            main_content_title = "주요 내용\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': main_content_title}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(main_content_title)}, 'textStyle': {'bold': True}, 'fields': 'bold'}})
            requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(main_content_title)}, 'paragraphStyle': {'shading': {'backgroundColor': {'color': {'rgbColor': {'red': 0.91, 'green': 0.95, 'blue': 1.0}}}}}, 'fields': 'shading'}})
            index += len(main_content_title)
            
            main_content_body = f"{main_content}\n\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': main_content_body}})
            index += len(main_content_body)
            
            print(main_content_body)

            # 시사점 및 전망 섹션
            implications_title = "시사점 및 전망\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': implications_title}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(implications_title)}, 'textStyle': {'bold': True}, 'fields': 'bold'}})
            requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(implications_title)}, 'paragraphStyle': {'shading': {'backgroundColor': {'color': {'rgbColor': {'red': 1.0, 'green': 0.96, 'blue': 0.9}}}}}, 'fields': 'shading'}})
            index += len(implications_title)

            implications_body = f"{implications}\n\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': implications_body}})
            index += len(implications_body)

        # 3. 일괄 업데이트 실행
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        
        return document_url, document_title
    except Exception as e:
        print(f"  (오류) 구글 문서 생성/스타일링 실패: {e}")
        return None, None

# ==============================================================================
# --- 6. Gmail 전송 함수 (템플릿 및 파싱 로직 수정) ---
# ==============================================================================
def send_gmail_report(report_title, analyzed_data, doc_url, other_news):
    # ... (생략) ...
    news_items_html = ""
    for i, data in enumerate(analyzed_data):
        # ... (분석 결과 파싱 로직) ...

        # ✅ 해결책: += 연산자를 사용하여 HTML 내용을 계속 누적합니다.
        news_items_html += f"""
        <div class="news-item">
            <div class="news-header">
                <h3 class="news-title">[{i+1}] {data['title']}</h3>
                <div class="news-meta">
                    <span><strong>출처:</strong> {data['source']}</span>
                    <span><strong>발행일:</strong> {data['published']}</span>
                    <span><a href="{data['link']}" target="_blank">원문 기사 보기 &rarr;</a></span>
                </div>
            </div>
            <div class="analysis-container">
                <div class="analysis-section summary">
                    <div class="analysis-title"><span class="icon">📝</span><strong>주요 내용</strong></div>
                    <p class="analysis-text">{main_content.replace('\n', '<br>')}</p>
                </div>
                <div class="analysis-section implications">
                    <div class="analysis-title"><span class="icon">💡</span><strong>시사점 및 전망</strong></div>
                    <p class="analysis-text">{implications.replace('\n', '<br>')}</p>
                </div>
            </div>
        </div>"""

    # 2. 기타 뉴스 HTML 생성 (변경 없음)
    other_news_html = ""
    if other_news:
        other_news_html += """
        <div class="other-news-section">
            <h2>기타 수집된 뉴스</h2>
            <ul class="other-news-list">
        """
        for item in other_news:
            other_news_html += f'<li><a href="{item["link"]}" target="_blank" class="other-news-link"><span class="other-news-title">{item["title"]}</span><span class="other-news-source">({item["source"]})</span></a></li>'
        
        other_news_html += "</ul></div>"


    # 3. 전체 이메일 본문 조합 (템플릿 수정)
    html_body = f"""
    <!DOCTYPE html>
    <html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ICT 주요기술 동향 리포트</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');
        body {{ margin: 0; padding: 0; background-color: #f4f7fa; font-family: 'Noto Sans KR', sans-serif; }}
        .email-container {{ max-width: 700px; margin: 20px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.05); border: 1px solid #e9e9e9; }}
        .header {{ background: linear-gradient(135deg, #1D2B4A 0%, #2C3E6A 100%); color: #ffffff; padding: 40px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 700; }} .header p {{ margin: 8px 0 0; font-size: 16px; font-weight: 300; opacity: 0.8; }}
        .disclaimer {{ font-size: 12px; opacity: 0.7; font-style: italic; margin-top: 15px;}}
        .main-content {{ padding: 40px; }}
        .report-intro {{ text-align: center; padding-bottom: 30px; border-bottom: 1px solid #eaeaea; margin-bottom: 30px; }}
        .button {{ display: inline-block; background: linear-gradient(135deg, #4A6DFF 0%, #6284FF 100%); color: #ffffff; padding: 14px 28px; border-radius: 50px; text-decoration: none; font-weight: 500; font-size: 15px; transition: all 0.3s ease; box-shadow: 0 4px 15px rgba(74, 109, 255, 0.3); }}
        .button:hover {{ transform: translateY(-2px); box-shadow: 0 8px 25px rgba(74, 109, 255, 0.4); }}
        .news-item {{ border: 1px solid #e9e9e9; border-radius: 12px; margin-bottom: 25px; overflow: hidden; transition: all 0.3s ease; }}
        .news-item:hover {{ transform: translateY(-2px); box-shadow: 0 8px 25px rgba(0, 0, 0, 0.07); }}
        .news-header {{ padding: 25px; }} .news-title {{ font-size: 20px; font-weight: 700; color: #1D2B4A; margin: 0 0 15px; }}
        .news-meta {{ font-size: 13px; color: #777; }} .news-meta a {{ color: #4A6DFF; text-decoration: none; font-weight: 500; }} .news-meta span {{ margin-right: 15px; }}
        .analysis-container {{ padding: 0 25px 25px 25px; border-top: 1px solid #e9e9e9; background-color: #f8f9fc; }}
        .analysis-section {{ padding: 20px; border-radius: 8px; margin-top: 15px; }}
        .analysis-section.summary {{ background-color: #e9f3ff; border-left: 4px solid #4A6DFF; }}
        .analysis-section.implications {{ background-color: #fff6e9; border-left: 4px solid #ff9f43; }}
        .analysis-title {{ display: flex; align-items: center; font-size: 16px; font-weight: 700; color: #1D2B4A; margin-bottom: 10px; }}
        .analysis-title .icon {{ font-size: 20px; margin-right: 10px; }}
        .analysis-text {{ font-size: 14px; line-height: 1.7; color: #333; margin: 0; }}
        .other-news-section {{ margin-top: 40px; padding-top: 30px; border-top: 1px solid #eaeaea; }}
        .other-news-section h2 {{ font-size: 20px; font-weight: 700; color: #1D2B4A; margin-bottom: 20px; text-align: center; }}
        .other-news-list {{ list-style-type: none; padding: 0; margin: 0; }}
        .other-news-list li {{ border-radius: 8px; margin-bottom: 5px; border-left: 3px solid #ccc; background-color: #f8f9fc; transition: background-color 0.2s ease; }}
        .other-news-list li:hover {{ background-color: #f1f3f8; }}
        .other-news-link {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 15px; text-decoration: none; color: inherit; }}
        .other-news-title {{ color: #333; font-size: 14px; }}
        .other-news-source {{ color: #888; font-size: 12px; white-space: nowrap; margin-left: 15px; }}
        .footer {{ text-align: center; padding: 30px; background-color: #f4f7fa; font-size: 13px; color: #999; }}
    </style></head>
    <body><div class="email-container">
        <div class="header"><h1>{report_title}</h1><p>오늘의 핵심 기술 뉴스를 AI가 분석해드립니다.</p><p class="disclaimer">※ 본 보고서의 내용은 AI가 생성한 분석으로, 개인적인 의견을 포함하지 않습니다.</p></div>
        <div class="main-content">
            <div class="report-intro">
                <a href="{doc_url}" class="button" target="_blank">📄 전체 보고서 보기</a>
            </div>
            {news_items_html}
            {other_news_html}
        </div>
        <div class="footer"><p>본 리포트는 AI 기술을 활용해 자동 생성된 분석 보고서입니다.</p><p>Powered by Advanced IRONAGE AI Analytics</p></div>
    </div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = report_title
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECEIVER_EMAIL)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, GMAIL_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print(f"  > ✅ 이메일이 {', '.join(RECEIVER_EMAIL)} 주소로 성공적으로 발송되었습니다.")
    except Exception as e:
        print(f"  (오류) 이메일 발송에 실패했습니다: {e}")

# ==============================================================================
# --- 7. 메인 실행 부분 (기존과 동일) ---
# ==============================================================================
if __name__ == "__main__":
    print("==============================================")
    print("🤖 AI 뉴스 리포트 자동 생성 스크립트를 시작합니다.")
    print("==============================================")
    
    print("\n[🚀 작업 시작] 뉴스 수집 및 중복 제거를 시작합니다...")
    unique_news_items = get_news_data()
    print(f"  > 총 {len(unique_news_items)}개의 고유한 뉴스를 1차 수집했습니다.")
    
    news_to_analyze = filter_news_by_ai(unique_news_items)
    print(f"  > AI가 최종 선별한 {len(news_to_analyze)}개의 핵심 뉴스를 심층 분석합니다.")
    
    analyzed_links = {item['link'] for item in news_to_analyze}
    other_news = [item for item in unique_news_items if item['link'] not in analyzed_links]
    
    analyzed_results = []
    if news_to_analyze:
        print("\n[🚀 작업 중] 선택된 뉴스에 대한 심층 분석을 시작합니다...")
        for i, item in enumerate(news_to_analyze):
            print(f"  ({i+1}/{len(news_to_analyze)}) 분석 중: {item['title'][:40]}...")
            analysis = analyze_news_with_ai(item)
            item['analysis_result'] = analysis
            analyzed_results.append(item)
            
    if analyzed_results:
        print("\n[🚀 작업 중] 구글 문서 보고서를 생성하고 있습니다...")
        generated_doc_url, report_title = generate_google_doc_report(analyzed_results)
        
        if report_title:
            print("\n[🚀 작업 중] 생성된 리포트를 이메일로 발송합니다...")
            send_gmail_report(report_title, analyzed_results, generated_doc_url, other_news)
            print("   (정보) 이메일 발송은 주석 처리되어 있습니다. 필요 시 주석을 해제하세요.")

    print("\n==============================================")
    print("🎉 모든 작업이 완료되었습니다!")
    print("==============================================")





