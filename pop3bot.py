import poplib
import email.parser
import config
import logging
logger = logging.getLogger('pop3bot')

def mkmailbox(debug=0):
    username = config.username
    password = config.password

    mailbox = poplib.POP3_SSL('pop.googlemail.com', '995') 
    mailbox.set_debuglevel(debug)

    mailbox.user(username)
    mailbox.pass_(password)

    return mailbox

def mail_generator(mailbox):
    """ RETRieves the contents of mails, yields those
        and DELEtes them before the next mail is RETRieved """
    nmails, octets = mailbox.stat()
    for i in range(1,nmails+1):
        # use TOP rather than REPR; gmail (sometimes?) interprets REPR'd
        # messages as read and does not report them again (sigh)
        yield "\n".join(mailbox.top(i, 1000)[1])
        mailbox.dele(i)

def message_generator(mailbox):
    p = email.parser.Parser()
    for mail in mail_generator(mailbox):
        mail = p.parsestr(mail)
        yield mail, mail.get_payload(decode=True)

def gerritmail_generator(mailbox):
    for message, contents in message_generator(mailbox):
        gerrit_data = dict((k,v) for (k,v) in message.items() if k.startswith('X-Gerrit'))
        gerrit_data.update(dict(line.split(": ", 1) for line in contents.split('\n') if (line.startswith("Gerrit-") and ": " in line)))

        if gerrit_data:
            yield gerrit_data

import gerrit_rest
g = gerrit_rest.GerritREST('https://gerrit.wikimedia.org/r')

def get_changeset(changeid, o=['CURRENT_REVISION', 'CURRENT_FILES', 'DETAILED_ACCOUNTS']):
        matchingchanges = g.changes(changeid, n=1, o=o)
        if matchingchanges:
            return matchingchanges[0]
        else:
            return None

def new_changeset_generator(mailbox):
    for mail in gerritmail_generator(mailbox):
        if mail.get('X-Gerrit-MessageType', '') != 'newchange':
            continue
        if mail.get('Gerrit-PatchSet', '') != '1':
            continue
        print "(getting ", mail['X-Gerrit-Commit'], ")"
        matchingchange = get_changeset(mail['X-Gerrit-Commit'])
        if matchingchange:
            yield matchingchange

def filter_reviewers(reviewers, owner_name, changeset_number):
    if owner_name.lower() == u'l10n-bot':
        logger.debug('Skipping l10n-bot')
        return

    i = 0
    for (reviewer, modulo) in reviewers:
        if reviewer.lower() == owner_name.lower():
            logger.debug('Skipping owner %r' % reviewer)
            continue

        if ((changeset_number + i) % modulo == 0):
            yield reviewer
        else:
            logger.debug('Skipping %r due to modulo')

from add_reviewer import ReviewerFactory, add_reviewers
RF = ReviewerFactory()

def get_reviewers_for_changeset(changeset):
    owner = changeset['owner']['name']

    changes = changeset['revisions'].values()[0]['files']
    changedfiles = [k for (k,v) in changes.items()]
    try:
        addedfiles = [k for (k,v) in changes.items() if 'status' in v and v['status'] == 'A']
    except Exception, e:
        print e, repr(changes.items())
        addedfiles = []

    project = changeset['project']
    number = changeset['_number']

    print ""
    print "Processing changeset ", changeset['change_id'], changeset['subject'], 'by', owner
    for f in changedfiles:
        if f in addedfiles:
            print "A",
        else:
            print "u",
        print f

    if changeset['status'] in [u'ABANDONED', u'MERGED']:
        print "Changeset was ", changeset['status'], "; not adding reviewers"
        return []

    reviewers = filter_reviewers(RF.reviewer_generator(project, changedfiles, addedfiles), owner, number)

    return reviewers

if __name__ == "__main__":
    mailbox = mkmailbox(0)
    nmails, octets = mailbox.stat()

    print "%i e-mails to process (%i kB)" % (nmails, octets/1024)

    try:
        for j,changeset in enumerate(new_changeset_generator(mailbox)):
            reviewers = get_reviewers_for_changeset(changeset)
            add_reviewers(changeset['current_revision'], reviewers)
    finally:
        # flush succesfully processed emails
        mailbox.quit()
