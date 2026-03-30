
import imaplib
import email
from email.header import decode_header
from typing import List, Dict, Any

def fetch_new_emails(config: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    imap_server = config.get("host", "imap.gmail.com")
    email_user = config.get("user")
    email_password = config.get("password")
    
    if not email_user or not email_password:
        print("Email user or password not found in config.")
        return []

    new_messages = []
    
    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_user, email_password)
        mail.select('inbox')
        
        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK':
            return []

        # Process emails, but limit to prevent long-running worker tasks
        all_ids = messages[0].split()
        to_process = all_ids[:limit]

        for num in to_process:
            status, data = mail.fetch(num, '(RFC822)')
            if status != 'OK':
                continue

            for response_part in data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject_data = decode_header(msg['Subject'])[0]
                    subject, encoding = subject_data
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else 'utf-8', errors='replace')

                    from_ = msg.get('From', '')
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            charset = part.get_content_charset() or 'utf-8'
                            content_disposition = str(part.get('Content-Disposition'))

                            if "attachment" not in content_disposition:
                                if "text/plain" in content_type:
                                    payload = part.get_payload(decode=True)
                                    body = payload.decode(charset, errors='replace')
                                    break
                        else: # if no plain text part, find html
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                charset = part.get_content_charset() or 'utf-8'
                                content_disposition = str(part.get('Content-Disposition'))
                                if "attachment" not in content_disposition and "text/html" in content_type:
                                    import re
                                    payload = part.get_payload(decode=True)
                                    body = payload.decode(charset, errors='replace')
                                    body = re.sub('<[^<]+?>', '', body)
                                    break
                    else:
                        charset = msg.get_content_charset() or 'utf-8'
                        payload = msg.get_payload(decode=True)
                        body = payload.decode(charset, errors='replace')

                    new_messages.append({
                        "from": from_,
                        "subject": subject,
                        "body": body
                    })
            mail.store(num, '+FLAGS', '\\Seen')

        mail.close()
        mail.logout()
    except Exception as e:
        print(f"Error fetching emails: {e}")

    return new_messages
