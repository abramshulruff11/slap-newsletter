"""
SLAP Newsletter — Email HTML Builder
Converts newsletter_draft.html into email-safe HTML for MailerLite delivery.

Key transformations:
- Applies inline styles (MailerLite strips <style> tags)
- Converts tweet blockquotes into styled table-based cards
- Preserves GIF/meme <img> tags with email-safe attributes
- Wraps content in a 600px email table layout
"""

import re
from bs4 import BeautifulSoup, Tag, NavigableString


# Inline style definitions — applied to every element
S = {
    'h1': (
        'font-family: Arial, sans-serif; font-size: 26px; font-weight: bold; '
        'color: #1a1a1a; line-height: 1.3; margin: 32px 0 12px 0; padding: 0;'
    ),
    'h2': (
        'font-family: Arial, sans-serif; font-size: 20px; font-weight: bold; '
        'color: #1a1a1a; line-height: 1.3; margin: 28px 0 10px 0; '
        'padding: 0 0 6px 0; border-bottom: 2px solid #e94560;'
    ),
    'p': (
        'font-family: Georgia, serif; font-size: 16px; line-height: 1.7; '
        'color: #1a1a1a; margin: 0 0 14px 0; padding: 0;'
    ),
    'hr': 'border: none; border-top: 3px solid #e94560; margin: 32px 0;',
    'img': 'max-width: 100%; display: block; margin: 16px auto; height: auto;',
}

EMAIL_WRAPPER = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <title>SLAP Newsletter</title>
</head>
<body style="margin: 0; padding: 0; background-color: #ffffff;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color: #ffffff;">
    <tr>
      <td align="center" style="padding: 20px 10px;">
        <table cellpadding="0" cellspacing="0" border="0"
               style="max-width: 600px; width: 100%;">
          <tr>
            <td style="padding: 0 10px;">
{content}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _inline_links(html_str: str) -> str:
    """Apply email-safe inline styles to any <a> tags."""
    return re.sub(
        r'<a\s[^>]*href=(["\'])(.*?)\1[^>]*>',
        lambda m: f'<a href={m.group(1)}{m.group(2)}{m.group(1)} '
                  f'style="color: #e94560; text-decoration: none;">',
        html_str
    )


def _tweet_card(element: Tag) -> str:
    """Convert a <blockquote class="tweet"> into an email-safe table card."""
    # Handle
    strong = element.find('strong')
    handle = strong.get_text(strip=True) if strong else '@unknown'

    # Tweet URL — strip nitter #m suffix
    link_tag = element.find('a')
    tweet_url = '#'
    if link_tag:
        raw_url = link_tag.get('href', '#')
        tweet_url = raw_url[:-2] if raw_url.endswith('#m') else raw_url

    # Tweet text: all lines except handle (first) and "View tweet" (last)
    all_lines = [
        line.strip()
        for line in element.get_text(separator='\n').split('\n')
        if line.strip()
    ]
    body_lines = all_lines[1:-1] if len(all_lines) > 2 else []
    tweet_text = '<br>'.join(body_lines) if body_lines else ''

    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 16px 0; border-collapse: collapse;">'
        '<tr><td style="background-color: #f8f9fa; border-left: 4px solid #1da1f2; '
        'padding: 14px 18px; font-family: Arial, sans-serif; font-size: 14px; '
        'line-height: 1.5;">'
        f'<div style="color: #1da1f2; font-weight: bold; margin-bottom: 6px;">{handle}</div>'
        f'<div style="color: #1a1a1a; margin-bottom: 10px;">{tweet_text}</div>'
        f'<a href="{tweet_url}" style="color: #1da1f2; font-size: 12px; '
        'text-decoration: none;">View on Twitter &#8594;</a>'
        '</td></tr></table>'
    )


def _img_tag(src: str, alt: str = 'image') -> str:
    return (
        f'<img src="{src}" alt="{alt}" width="100%" '
        f'style="{S["img"]}" border="0">'
    )


def build_email_html(draft_html: str) -> str:
    """
    Convert newsletter_draft.html → email-safe HTML for MailerLite.
    Input: the full draft HTML string (with inline GIFs/memes already embedded).
    Output: a complete email-safe HTML document string.
    """
    # Strip editor flag comments
    draft_html = re.sub(r'<!--.*?-->', '', draft_html, flags=re.DOTALL)

    soup = BeautifulSoup(draft_html, 'html.parser')
    body = soup.find('body') or soup

    blocks = []

    for element in body.children:

        # Bare text nodes (non-whitespace)
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                blocks.append(f'<p style="{S["p"]}">{text}</p>')
            continue

        if not isinstance(element, Tag):
            continue

        tag = element.name

        if tag == 'h1':
            blocks.append(f'<h1 style="{S["h1"]}">{element.decode_contents()}</h1>')

        elif tag == 'h2':
            blocks.append(f'<h2 style="{S["h2"]}">{element.decode_contents()}</h2>')

        elif tag == 'p':
            inner = _inline_links(element.decode_contents().strip())
            if inner:
                blocks.append(f'<p style="{S["p"]}">{inner}</p>')

        elif tag == 'blockquote':
            if 'tweet' in element.get('class', []):
                blocks.append(_tweet_card(element))

        elif tag == 'hr':
            blocks.append(f'<hr style="{S["hr"]}">')

        elif tag == 'img':
            src = element.get('src', '')
            if src:
                blocks.append(_img_tag(src, element.get('alt', 'image')))

        elif tag == 'div':
            # GIF/meme wrappers — pull out the img if present
            img = element.find('img')
            if img and img.get('src'):
                blocks.append(_img_tag(img['src'], img.get('alt', 'image')))
            # Unfilled placeholder divs: skip silently

    return EMAIL_WRAPPER.format(content='\n'.join(blocks))


if __name__ == '__main__':
    # Quick local test: reads newsletter_draft.html, writes newsletter_email.html
    from pathlib import Path
    draft = Path('newsletter_draft.html').read_text(encoding='utf-8')
    email_html = build_email_html(draft)
    Path('newsletter_email.html').write_text(email_html, encoding='utf-8')
    print(f'Written newsletter_email.html ({len(email_html):,} bytes)')
    print('Open it in a browser to preview the email layout.')
