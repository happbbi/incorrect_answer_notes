import io
import os
import re

import fitz  # PyMuPDF
import streamlit as st
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# 분할 로직
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r"^(\d{1,3})(?:[.)]|$)")  # "1", "1.", "1)" 형태


def setup_tessdata():
    """TESSDATA_PREFIX가 없으면 흔한 설치 경로에서 찾아 자동 설정."""
    if os.environ.get("TESSDATA_PREFIX"):
        return
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tessdata",
        r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
        "/opt/homebrew/share/tessdata",
        "/usr/local/share/tessdata",
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    ]
    for p in candidates:
        if os.path.isdir(p):
            os.environ["TESSDATA_PREFIX"] = p
            return


setup_tessdata()

# OCR이 숫자를 문자로 잘못 읽는 경우 보정 (l→1, O→0 등)
_OCR_FIX = str.maketrans({"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})
_OCR_NUMLIKE = re.compile(r"^[0-9lIO|oSB]{1,3}[.)]?$")


def fix_ocr_digits(tok):
    return tok.translate(_OCR_FIX) if _OCR_NUMLIKE.match(tok) else tok


def get_page_words(page, force_ocr=False):
    """(words, used_ocr) 반환. 글자 레이어가 없으면(스캔본) 자동으로 OCR."""
    if not force_ocr:
        words = page.get_text("words")
        if len(words) >= 5:
            return words, False
    # 페이지 크기에 맞춰 OCR 해상도 조절 (너무 큰 스캔 이미지 방지)
    dpi = int(max(72, min(300, 2500 / (page.rect.width / 72))))
    tp = page.get_textpage_ocr(language="kor+eng", dpi=dpi, full=True)
    return page.get_text("words", textpage=tp), True


def pix_to_jpeg_bytes(pix):
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def find_question_starts(col_words, x0, margin_pt, expected, used_ocr, max_skip=2, lenient=False):
    band = x0 + margin_pt

    # 0) 같은 줄에서 쪼개진 두 자리 번호 합치기: "1" + "1." -> "11."
    merged = []
    for w in col_words:
        if merged:
            p = merged[-1]
            h = max(1.0, p[3] - p[1])
            if (p[0] < band and abs(w[1] - p[1]) < 0.5 * h and 0 <= w[0] - p[2] < 0.8 * h
                    and re.fullmatch(r"\d", p[4]) and re.fullmatch(r"\d[.)]?", w[4])):
                merged[-1] = (p[0], min(p[1], w[1]), w[2], max(p[3], w[3]), p[4] + w[4]) + tuple(p[5:])
                continue
        merged.append(w)

    cands = []
    for w in merged:
        tok = fix_ocr_digits(w[4]) if used_ocr else w[4]
        m = NUM_RE.match(tok)
        if m and w[0] < band:
            cands.append((int(m.group(1)), w))
    if not cands:
        return [], expected

    # 번호 열 찾기: x가 비슷한 후보끼리 묶고, 번호가 이어지는 가장 왼쪽 열 선택
    tol = max(4.0, margin_pt * 0.1)
    clusters = []
    for idx in sorted(range(len(cands)), key=lambda i: cands[i][1][0]):
        x = cands[idx][1][0]
        if clusters and x - clusters[-1][0] <= tol:
            clusters[-1][1].append(idx)
        else:
            clusters.append([x, [idx]])

    def simulate(indices, exp):
        accepted = 0
        for i in sorted(indices):
            n = cands[i][0]
            if exp <= n <= exp + max_skip:
                accepted += 1
                exp = n + 1
        return accepted

    sims = [simulate(c[1], expected) for c in clusters]
    chosen = next((k for k, s in enumerate(sims) if s >= 2), None)
    if chosen is None:
        chosen = max(range(len(clusters)), key=lambda k: sims[k])
    keep = set(clusters[chosen][1])

    # 기울어진 스캔 보정
    seed = [cands[i][1] for i in sorted(keep)]
    if len(seed) >= 3:
        ys = [s[1] for s in seed]
        xs = [s[0] for s in seed]
        cnt = len(seed)
        my, mx = sum(ys) / cnt, sum(xs) / cnt
        var = sum((yy - my) ** 2 for yy in ys)
        slope = sum((yy - my) * (xx - mx) for yy, xx in zip(ys, xs)) / var if var else 0.0
        if abs(slope) < 0.1:
            icpt = mx - slope * my
            keep = {i for i, (_, cw) in enumerate(cands)
                    if abs(cw[0] - (slope * cw[1] + icpt)) <= tol}

    def next_num(idx):
        for j in range(idx + 1, len(cands)):
            if j in keep:
                return cands[j][0]
        return None

    results, last_y = [], None
    for idx, (n, w) in enumerate(cands):
        if idx not in keep:
            results.append((n, w, False, "번호 열이 아님 (x 위치가 다름)"))
            continue
        h = max(1.0, w[3] - w[1])                      # 글자 높이
        gap = None if last_y is None else w[1] - last_y  # 앞 문항 시작과의 거리
        if gap is not None and gap < 0.5 * h:
            continue  # 같은 자리에서 두 번 잡힘 -> 조용히 무시
        nxt = next_num(idx)
        if n == expected:
            label, note = n, ""
        elif n < expected and nxt == expected and gap is not None and gap < 3 * h:
            results.append((n, w, False, f"앞 문항과 너무 가까운 중복 (기대 {expected}번)"))
            continue
        elif nxt == n + 1 or (nxt is None and expected < n <= expected + max_skip):
            label, note = n, f"번호 기준 재조정 ({expected}→{n})"
        else:
            label, note = expected, f"OCR이 {n}번으로 읽음 → {expected}번으로 처리"
        results.append((label, w, True, note))
        expected = label + 1
        last_y = w[1]
    return results, expected

def split_pdf_by_numbers(pdf_bytes, two_col=True, margin=50, start_no=1, zoom=2.5, pad=6,
                         force_ocr=False, max_skip=2, lenient=False):
    """문항 번호를 기준으로 PDF를 문제별 이미지로 자른다. [{page, no, data}, ...]"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    results = []
    expected = start_no  # 페이지/단을 넘어서도 번호가 이어진다고 가정

    for pno, page in enumerate(doc, start=1):
        W, H = page.rect.width, page.rect.height
        cols = [(0, W / 2), (W / 2, W)] if two_col else [(0, W)]
        sc = W / 595  # A4 기준 비율 (큰 스캔 이미지일 때 pt 단위 값을 보정)
        all_words, used_ocr = get_page_words(page, force_ocr)

        for x0, x1 in cols:
            words = [w for w in all_words if x0 - 1 <= w[0] < x1]
            words.sort(key=lambda w: (round(w[1]), w[0]))

            res, expected = find_question_starts(
                words, x0, margin * sc, expected, used_ocr, max_skip=max_skip, lenient=lenient
            )
            starts = [(n, w[1]) for n, w, ok, _ in res if ok]

            for i, (no, y) in enumerate(starts):
                top = max(0, y - pad * sc)
                bottom = (starts[i + 1][1] - pad * sc) if i + 1 < len(starts) else H - 30 * sc
                if bottom - top < 20 * sc:
                    continue
                z = min(zoom, 1400 / (x1 - x0))  # 결과 이미지가 너무 커지지 않게
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(z, z),
                    clip=fitz.Rect(x0, top, x1, bottom),
                )
                results.append({"page": pno, "no": no, "data": pix_to_jpeg_bytes(pix)})
    return results


def debug_numbers(pdf_bytes, two_col=True, margin=50, start_no=1, force_ocr=False, max_pages=2,
                  max_skip=2, lenient=False):
    """자르지 않고, 번호 인식 결과만 이미지에 표시한다.
    초록 박스 = 문항 시작으로 인정, 빨강 박스 = 번호처럼 보이지만 순서가 안 맞아 제외,
    파란 세로선 = 번호 허용 범위의 오른쪽 경계, 회색 세로선 = 단 경계."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out, expected = [], start_no
    for pno, page in enumerate(doc, start=1):
        if pno > max_pages:
            break
        W, H = page.rect.width, page.rect.height
        sc = W / 595
        cols = [(0, W / 2), (W / 2, W)] if two_col else [(0, W)]
        words, used_ocr = get_page_words(page, force_ocr)
        lines = []
        for x0, x1 in cols:
            col_words = [w for w in words if x0 - 1 <= w[0] < x1]
            col_words.sort(key=lambda w: (round(w[1]), w[0]))
            res, expected = find_question_starts(
                col_words, x0, margin * sc, expected, used_ocr, max_skip=max_skip, lenient=lenient
            )
            for n, w, ok, note in res:
                page.draw_rect(fitz.Rect(w[:4]), color=(0, 0.7, 0) if ok else (1, 0, 0), width=2 * sc)
                status = ("인정" + (f" ({note})" if note else "")) if ok else f"제외: {note}"
                lines.append(f"{pno}p 읽은 글자 '{w[4]}' → {n}번 : {status}")
            page.draw_line((x0 + margin * sc, 0), (x0 + margin * sc, H), color=(0, 0, 1), width=1 * sc)
        if two_col:
            page.draw_line((W / 2, 0), (W / 2, H), color=(0.5, 0.5, 0.5), width=1 * sc)
        z = min(1.5, 1400 / W)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z))
        out.append((pno, pix_to_jpeg_bytes(pix), used_ocr, lines))
    return out


def render_pdf_pages(pdf_bytes, zoom=2.0):
    """번호를 못 찾았을 때 쓰는 대체 방법: 페이지 전체를 이미지로."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [
        {
            "page": i,
            "no": None,
            "data": pix_to_jpeg_bytes(
                p.get_pixmap(matrix=fitz.Matrix(min(zoom, 1400 / p.rect.width), min(zoom, 1400 / p.rect.width)))
            ),
        }
        for i, p in enumerate(doc, start=1)
    ]


def image_to_jpeg_bytes(file_bytes):
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 상태 관리 / 유틸
# ---------------------------------------------------------------------------
def add_problem(subject, name, data):
    lst = st.session_state["uploaded_problems"].setdefault(subject, [])
    new_id = max([p["id"] for p in lst], default=0) + 1
    m = re.search(r"_(\d+)번(?: \(p\d+\))?$", name)
    pg = re.search(r"_(p\d+) \(페이지 전체\)$", name)
    label = f"{m.group(1)}번" if m else (f"{pg.group(1)} 전체" if pg else name)
    lst.append({
        "id": new_id, "file_name": name, "type": "image", "data": data, "checked": False,
        "file": st.session_state.get("current_file", "파일"),
        "label": label,
    })

def build_pdf(problems):
    """체크한 문항을 한 문제당 한 페이지(A4)로 묶어 PDF 생성."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    m = 40
    box_w, box_h = pw - 2 * m, ph - 2 * m
    for p in problems:
        img = Image.open(io.BytesIO(p["data"])).convert("RGB")
        iw, ih = img.size
        scale = min(box_w / iw, box_h / ih)
        w, h = iw * scale, ih * scale
        c.drawImage(ImageReader(img), m + (box_w - w) / 2, ph - m - h, width=w, height=h)
        c.showPage()
    c.save()
    return buf.getvalue()

def on_check(subject, pid, key):
    for p in st.session_state["uploaded_problems"].get(subject, []):
        if p["id"] == pid:
            p["checked"] = st.session_state[key]


def set_all(subject, file, value):
    for p in st.session_state["uploaded_problems"].get(subject, []):
        if p.get("file", "파일") == file:
            p["checked"] = value
            st.session_state[f"tab4_chk_{subject}_{p['id']}"] = value
st.session_state.setdefault("subjects_list", ["수학", "영어", "국어"])
st.session_state.setdefault("uploaded_problems", {})  # {과목명: [문제 dict, ...]}

st.set_page_config(page_title="과목별 오답 문제 정리", page_icon="📚", layout="wide")
st.title("📚 과목별 오답 문제 정리 홈페이지")
st.markdown("PDF를 올리면 문항 번호를 기준으로 문제별로 잘라 과목별로 관리하는 오답노트입니다.")

tab1, tab2, tab3, tab4 = st.tabs([
    "1️⃣ 업로드 및 자동 분할",
    "2️⃣ 과목 관리",
    "3️⃣ 과목별 누적 현황",
    "4️⃣ 오답 체크 및 PDF 정리",
])

# ---------------------------------------------------------------------------
# [탭 1] 업로드 및 자동 분할
# ---------------------------------------------------------------------------
with tab1:
    st.header("📤 과목 선택 및 문제 파일 업로드")

    if not st.session_state["subjects_list"]:
        st.warning("등록된 과목이 없습니다. [2️⃣ 과목 관리] 탭에서 과목을 먼저 추가해주세요.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            selected_subject = st.selectbox("과목 선택", st.session_state["subjects_list"], key="tab1_subj")
        with col2:
            custom_filename = st.text_input("파일 이름", value="오답시험지", key="tab1_fname")

        uploaded_file = st.file_uploader(
            "문제 파일(PDF 또는 이미지)을 업로드하세요", type=["pdf", "png", "jpg", "jpeg"], key="tab1_uploader"
        )

        with st.expander("⚙️ 분할 설정 (잘 안 잘리면 여기를 조정하세요)"):
            two_col = st.checkbox("2단 편집 시험지 (좌/우 단을 나눠서 처리)", value=True)
            start_no = st.number_input("첫 문항 번호", min_value=0, max_value=999, value=1)
            margin = st.slider("번호 위치 허용 범위 (단 왼쪽 끝에서 pt)", 20, 150, 50)
            force_ocr = st.checkbox(
                "OCR 강제 사용 (스캔본인데 번호가 안 잡힐 때)",
                value=False,
                help="글자 레이어가 없는 스캔본은 자동으로 OCR을 쓰지만, 품질이 나쁜 글자 레이어가 있으면 이걸 켜세요.",
            )
            max_skip = st.slider(
                "번호 누락 허용 개수 (OCR이 연속으로 놓친 번호를 몇 개까지 건너뛸지)", 0, 5, 2
            )
            lenient = st.checkbox(
                "번호 열에 있는 숫자는 순서가 달라도 문항 시작으로 인정",
                value=False,
                help="OCR이 번호를 잘못 읽어서(예: 5를 6으로) 빨간 박스가 뜰 때 켜세요. "
                     "번호 열 안에 번호가 아닌 숫자가 섞여 있으면 문제가 더 쪼개질 수 있어요.",
            )

        if uploaded_file is not None and st.button("🧩 문항 번호 기준 자동 분할 실행"):
            raw = uploaded_file.getvalue()
            ext = uploaded_file.name.split(".")[-1].lower()
            base = custom_filename or "오답시험지"
            used = {p.get("file") for p in st.session_state["uploaded_problems"].get(selected_subject, [])}
            group, k = base, 2
            while group in used:
                group, k = f"{base} ({k})", k + 1
            st.session_state["current_file"] = group

            if ext == "pdf":
                try:
                    with st.spinner("문항을 찾는 중입니다... (스캔본은 OCR 때문에 오래 걸릴 수 있어요)"):
                        pieces = split_pdf_by_numbers(
                            raw, two_col=two_col, margin=margin,
                            start_no=int(start_no), force_ocr=force_ocr,
                            max_skip=max_skip, lenient=lenient,
                        )
                except Exception as e:
                    st.error(
                        f"PDF 처리 중 오류가 발생했습니다: {e}\n\n"
                        "OCR 관련 오류라면 Tesseract의 한국어 데이터(kor) 설치와 "
                        "TESSDATA_PREFIX 환경변수를 확인하세요."
                    )
                    pieces = []

                if pieces:
                    for p in pieces:
                        add_problem(selected_subject, f"{base}_{p['no']}번 (p{p['page']})", p["data"])
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    pages = render_pdf_pages(raw)
                    for p in pages:
                        add_problem(selected_subject, f"{base}_p{p['page']} (페이지 전체)", p["data"])
                    st.warning(
                        "문항 번호를 찾지 못해 페이지 전체를 그대로 저장했습니다. "
                        "스캔본이거나 번호 형식이 다를 수 있어요. 첫 문항 번호/2단 설정/허용 범위를 바꿔 다시 시도해 보세요."
                    )
            else:
                # 이미지 → 1페이지 PDF로 변환한 뒤 같은 방식(OCR 자동)으로 분할
                try:
                    img_pdf = fitz.open(stream=raw, filetype=ext).convert_to_pdf()
                    with st.spinner("이미지에서 문항을 찾는 중입니다... (OCR)"):
                        pieces = split_pdf_by_numbers(
                            img_pdf, two_col=two_col, margin=margin,
                            start_no=int(start_no), force_ocr=True,
                            max_skip=max_skip, lenient=lenient,
                        )
                except Exception as e:
                    st.error(
                        f"이미지 처리 중 오류가 발생했습니다: {e}\n\n"
                        "Tesseract의 한국어 데이터(kor)와 TESSDATA_PREFIX를 확인하세요."
                    )
                    pieces = []

                if pieces:
                    for p in pieces:
                        add_problem(selected_subject, f"{base}_{p['no']}번", p["data"])
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    add_problem(selected_subject, f"{base} (이미지 전체)", image_to_jpeg_bytes(raw))
                    st.warning("문항 번호를 찾지 못해 이미지 한 장 그대로 저장했습니다. 설정을 바꿔 다시 시도해 보세요.")

        if uploaded_file is not None and st.button("🔍 번호 인식 결과 미리보기 (처음 2페이지, 자르지 않음)"):
            raw = uploaded_file.getvalue()
            ext = uploaded_file.name.split(".")[-1].lower()
            try:
                pdf_in = raw if ext == "pdf" else fitz.open(stream=raw, filetype=ext).convert_to_pdf()
                with st.spinner("번호 인식 결과를 분석하는 중입니다..."):
                    results = debug_numbers(
                        pdf_in, two_col=two_col, margin=margin,
                        start_no=int(start_no), force_ocr=force_ocr or ext != "pdf",
                        max_skip=max_skip, lenient=lenient,
                    )
                st.caption("초록 박스=문항 시작으로 인정 / 빨강 박스=번호처럼 보이지만 제외 / 파란 선=번호 허용 범위 경계")
                for pno, img_bytes, used_ocr, lines in results:
                    st.markdown(f"**{pno}페이지** ({'OCR 사용' if used_ocr else '글자 레이어 사용'})")
                    st.image(img_bytes, use_container_width=True)
                    st.code("\n".join(lines) if lines else "허용 범위 안에서 번호처럼 보이는 글자를 찾지 못했습니다.")
            except Exception as e:
                st.error(f"미리보기 중 오류가 발생했습니다: {e}")

        st.markdown("---")
        st.subheader("👀 현재 과목에 등록된 문항 목록")
        problems = st.session_state["uploaded_problems"].get(selected_subject, [])
        if problems:
            st.write(f"총 {len(problems)}개의 문항이 등록되어 있습니다.")
            grid = st.columns(3)
            for i, prob in enumerate(problems):
                with grid[i % 3]:
                    st.caption(f"[ID {prob['id']}] {prob['file_name']}")
                    st.image(Image.open(io.BytesIO(prob["data"])), use_container_width=True)
                    if st.button("🗑️ 삭제", key=f"del_{selected_subject}_{prob['id']}"):
                        st.session_state["uploaded_problems"][selected_subject] = [
                            p for p in problems if p["id"] != prob["id"]
                        ]
                        st.rerun()
        else:
            st.info(f"'{selected_subject}' 과목에 등록된 문제가 없습니다.")

# ---------------------------------------------------------------------------
# [탭 2] 과목 관리
# ---------------------------------------------------------------------------
with tab2:
    sk1, sk2 = st.columns(2)

    with sk1:
        st.header("➕ 과목 추가")
        new_subject = st.text_input("추가할 과목명", placeholder="과목명 입력", key="tab2_input_add")
        if st.button("과목 추가하기", key="tab2_btn_add"):
            if not new_subject:
                st.warning("과목명을 입력해주세요.")
            elif new_subject in st.session_state["subjects_list"]:
                st.warning("이미 존재하는 과목입니다.")
            else:
                st.session_state["subjects_list"].append(new_subject)
                st.rerun()

    with sk2:
        st.header("✏️ 과목 수정 및 삭제")
        if not st.session_state["subjects_list"]:
            st.info("편집할 과목이 없습니다.")
        else:
            target = st.selectbox("수정/삭제할 과목", st.session_state["subjects_list"], key="tab2_select_target")
            rename_val = st.text_input("변경할 과목명", value=target, key="tab2_input_rename")

            b1, b2 = st.columns(2)
            with b1:
                if st.button("과목명 수정", key="tab2_btn_rename"):
                    if not rename_val:
                        st.warning("변경할 과목명을 입력해주세요.")
                    elif rename_val in st.session_state["subjects_list"]:
                        st.warning("이미 존재하는 과목명입니다.")
                    else:
                        idx = st.session_state["subjects_list"].index(target)
                        st.session_state["subjects_list"][idx] = rename_val
                        if target in st.session_state["uploaded_problems"]:
                            st.session_state["uploaded_problems"][rename_val] = \
                                st.session_state["uploaded_problems"].pop(target)
                        st.rerun()
            with b2:
                if st.button("과목 삭제", key="tab2_btn_delete", type="primary"):
                    st.session_state["subjects_list"].remove(target)
                    st.session_state["uploaded_problems"].pop(target, None)
                    st.rerun()

    st.markdown("---")
    st.subheader("📌 현재 등록된 과목")
    for subj in st.session_state["subjects_list"]:
        st.markdown(f"- **{subj}**")

# ---------------------------------------------------------------------------
# [탭 3] 과목별 누적 현황
# ---------------------------------------------------------------------------
with tab3:
    st.header("📊 과목별 오답 체크 현황")
    st.markdown("오답노트로 만들려고 체크한 문항 수입니다.")
    if not st.session_state["subjects_list"]:
        st.info("등록된 과목이 없습니다.")
    else:
        for subj in st.session_state["subjects_list"]:
            probs = st.session_state["uploaded_problems"].get(subj, [])
            by_file = {}
            for p in probs:
                if p.get("checked"):
                    f = p.get("file", "파일")
                    by_file[f] = by_file.get(f, 0) + 1
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"### 📘 {subj}")
            c2.metric("체크한 오답 문항", f"{sum(by_file.values())}개")
            if by_file:
                st.caption(" · ".join(f"{f}: {c}개" for f, c in by_file.items()))
            st.divider()
# ---------------------------------------------------------------------------
# [탭 4] 오답 체크 및 PDF 정리
# ---------------------------------------------------------------------------
with tab4:
    st.header("📄 오답 체크 및 PDF 정리")
    st.markdown("과목과 파일을 고른 뒤 틀린 문항 번호를 체크하세요.")

    if not st.session_state["subjects_list"]:
        st.info("등록된 과목이 없습니다.")
    else:
        pdf_subject = st.selectbox("과목 선택", st.session_state["subjects_list"], key="tab4_subj")
        subject_problems = st.session_state["uploaded_problems"].get(pdf_subject, [])

        if not subject_problems:
            st.info(f"'{pdf_subject}' 과목에 등록된 문제가 없습니다. 1번 탭에서 먼저 업로드해주세요.")
        else:
            files = list(dict.fromkeys(p.get("file", "파일") for p in subject_problems))
            sel_file = st.selectbox("파일 선택", files, key=f"tab4_file_{pdf_subject}")
            file_probs = [p for p in subject_problems if p.get("file", "파일") == sel_file]

            st.write(f"### 📝 '{sel_file}' 오답 체크")
            b1, b2, _ = st.columns([1, 1, 3])
            b1.button("전체 체크", key=f"all_on_{pdf_subject}_{sel_file}",
                      on_click=set_all, args=(pdf_subject, sel_file, True))
            b2.button("전체 해제", key=f"all_off_{pdf_subject}_{sel_file}",
                      on_click=set_all, args=(pdf_subject, sel_file, False))

            per_row = 5
            for start in range(0, len(file_probs), per_row):
                row = st.columns(per_row)
                for col, prob in zip(row, file_probs[start:start + per_row]):
                    key = f"tab4_chk_{pdf_subject}_{prob['id']}"
                    if key not in st.session_state:
                        st.session_state[key] = prob.get("checked", False)
                    col.checkbox(
                        prob.get("label", prob["file_name"]),
                        key=key,
                        on_change=on_check,
                        args=(pdf_subject, prob["id"], key),
                    )

            file_checked = sum(1 for p in file_probs if p.get("checked"))
            all_checked = sum(1 for p in subject_problems if p.get("checked"))
            m1, m2 = st.columns(2)
            m1.metric("이 파일에서 체크한 문항", f"{file_checked} / {len(file_probs)}개")
            m2.metric(f"'{pdf_subject}' 전체 체크 문항", f"{all_checked}개")

            st.markdown("---")
            scope = st.radio(
                "PDF에 넣을 범위",
                ["이 과목에서 체크한 문항 전체", "선택한 파일에서 체크한 문항만"],
                key=f"tab4_scope_{pdf_subject}",
            )
            targets = [
                p for p in subject_problems
                if p.get("checked")
                and (scope.startswith("이 과목") or p.get("file", "파일") == sel_file)
            ]

            pdf_key = f"generated_pdf_{pdf_subject}"
            if st.button("체크한 문항만 모아 오답노트 PDF 생성", key="tab4_pdf_btn"):
                if not targets:
                    st.warning("체크된 문항이 없습니다.")
                else:
                    st.session_state[pdf_key] = build_pdf(targets)
                    st.success(f"{len(targets)}개 문항으로 오답노트 PDF를 만들었습니다!")

            if pdf_key in st.session_state:
                st.download_button(
                    "📥 최종 오답노트 PDF 다운로드",
                    data=st.session_state[pdf_key],
                    file_name=f"{pdf_subject}_오답노트.pdf",
                    mime="application/pdf",
                    key="tab4_download",
                )