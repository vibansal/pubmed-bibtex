import click
import requests
import json
import re

"""
python script to get bibtex from pubmed (ID, keywords, etc), can also query biorxiv
starting code from github.com/zhuchcn/pubmed-bib
"""

ESEARCH_URL = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
EUROPEPMC_URL = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'

def searchPubMed(query, max_results=5):
    '''Search PubMed with a keyword query, return list of PMIDs.'''
    params = {
        'db': 'pubmed',
        'term': query,
        'retmax': max_results,
        'retmode': 'json',
    }
    response = requests.get(ESEARCH_URL, params=params).json()
    return response.get('esearchresult', {}).get('idlist', [])

def scoreReference(query_tokens, ref_data):
    '''Score a reference (CSL dict) against query tokens.
    Weights: author family name=3, year=3, journal word=2, title word=1.
    '''
    score = 0
    authors = ref_data.get('author', [])
    family_names = {a['family'].lower() for a in authors if 'family' in a}

    year = None
    if 'issued' in ref_data:
        year = str(ref_data['issued']['date-parts'][0][0])
    elif 'epub-date' in ref_data:
        year = str(ref_data['epub-date']['date-parts'][0][0])

    journal_words = set(re.findall(r'\w+', (ref_data.get('container-title') or '').lower()))
    journal_words |= set(re.findall(r'\w+', (ref_data.get('container-title-short') or '').lower()))
    title_words = set(re.findall(r'\w+', (ref_data.get('title') or '').lower()))

    for token in query_tokens:
        if token in family_names:
            score += 3
        elif year and token == year:
            score += 3
        elif token in journal_words:
            score += 2
        elif token in title_words:
            score += 1
    return score

def searchAndRank(query, top_k):
    '''Search PubMed, fetch metadata for a larger candidate pool, re-rank by
    keyword match score, and return the top_k (pmid, ref_data) pairs.'''
    candidate_count = min(top_k * 5, 50)
    pmids = searchPubMed(query, candidate_count)
    if not pmids:
        return []
    query_tokens = set(re.findall(r'\w+', query.lower()))
    scored = []
    for pmid in pmids:
        ref = getReference(pmid)
        if 'status' in ref[1] and ref[1]['status'] == 'error':
            continue
        score = scoreReference(query_tokens, ref[1])
        scored.append((score, ref))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ref for _, ref in scored[:top_k]]

def searchBiorxiv(query, max_results=5, rank=False):
    '''Search bioRxiv/medRxiv preprints via Europe PMC. Returns list of BibTeX strings.'''
    params = {
        'query': query,
        'resultType': 'core',
        'format': 'json',
        'src': 'ppr',                        # preprints only
        'pageSize': max_results if not rank else min(max_results * 5, 50),
    }
    data = requests.get(EUROPEPMC_URL, params=params).json()
    results = data.get('resultList', {}).get('result', [])
    if not results:
        return []
    if rank:
        query_tokens = set(re.findall(r'\w+', query.lower()))
        results = sorted(results, key=lambda r: _scoreBiorxiv(query_tokens, r), reverse=True)
        results = results[:max_results]
    return [_formatBiorxiv(r) for r in results]

def _scoreBiorxiv(query_tokens, result):
    '''Score a Europe PMC preprint result against query tokens.'''
    score = 0
    author_str = result.get('authorString', '').lower()
    # last names are before commas or before space-initiated initials
    family_names = set(re.findall(r'([a-z]+)(?:\s+[a-z]\.?)+(?:,|$)', author_str))
    year = str(result.get('pubYear', ''))
    journal_words = set(re.findall(r'\w+', result.get('journalTitle', '').lower()))
    title_words = set(re.findall(r'\w+', result.get('title', '').lower()))
    for token in query_tokens:
        if token in family_names:
            score += 3
        elif token == year:
            score += 3
        elif token in journal_words:
            score += 2
        elif token in title_words:
            score += 1
    return score

def _formatBiorxiv(result):
    '''Format a Europe PMC preprint result as a BibTeX @article entry.'''
    title = re.sub(r'<[^>]+>', '', result.get('title', ''))
    author_str = result.get('authorString', '').rstrip('.')
    year = result.get('pubYear', '')
    journal = result.get('journalTitle', 'bioRxiv')
    doi = result.get('doi', '')
    # build ref_id from first author last name + year + first title word
    first_author = re.split(r'[,\s]', author_str)[0].lower()
    first_word = re.findall(r'\w+', title)[0].lower() if title else 'preprint'
    ref_id = f'{first_author}{year}{first_word}'
    return f'''@article{{{ref_id},
    title={{{title}}},
    author={{{author_str}}},
    journal={{{journal}}},
    year={{{year}}},
    doi={{{doi}}},
    note={{Preprint}}
}}
'''

# Get a reference from PubMed database
def getReference(id):
    url_format = 'https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/pubmed/'
    # Parameters for query
    queryParams = {
        'format': 'csl',
        'id': id
    }
    # get a reference
    response = requests.get(url_format, params = queryParams).json()
    return id, response

# Format the reference to BibTex format
def formatReference(reference, use_short):
    id, reference = reference
    title = reference['title'] if 'title' in reference.keys() else ''
    # convert HTML tags to latex
    title = re.sub(r"<sub>(.+?)</sub>", r"$_{\1}$", title)
    title = re.sub(r"<sup>(.+?)</sup>", r"$^{\1}$", title)
    title = re.sub(r"<i>(.+?)</i>", r"\\textit{\1}", title)
    title = re.sub(r"<b>(.+?)</b>", r"\\textbf{\1}", title)
    title = re.sub(r"<[^>]+>", "", title)  # strip any remaining tags

    authors = reference['author'] if 'author' in reference.keys() else ''
    authorList = []
    for author in authors:
        if ('family' in author.keys()) and ('given' in author.keys()):
            authorList.append(author['family'] + ', ' + author['given'])
        elif ('family' in author.keys()) and ('given' not in author.keys()):
            authorList.append(author['family'])
        elif ('family' not in author.keys()) and ('given' in author.keys()):
            authorList.append(author['given'])
        else:
            continue

    journal_long = reference.get('container-title') or ''
    journal_short = reference.get('container-title-short') or ''
    volume = reference.get('volume') or ''
    page = reference.get('page') or ''
    
    if 'issued' in reference.keys():
        year = reference['issued']['date-parts'][0][0]
    elif 'epub-date' in reference.keys():
        year = reference['epub-date']['date-parts'][0][0]    

    ref_id = authors[0]["family"].lower() \
            if "family" in authors[0].keys() else authors[0]
    ref_id += str(year) + title.split(' ')[0].lower()

    output = f'''@article{{{ ref_id },
    title={{{title}}},
    author={{{' and '.join(authorList)}}},
    {"journal-long" if use_short else "journal"}={{{journal_long}}},
    {"journal" if use_short else "journal-short"}={{{journal_short}}},
    volume={{{volume}}},
    pages={{{page}}},
    year={{{year}}},
    PMID={{{id}}}
}}
'''
    return output

def showReference(id, use_short):
    '''
    Get the BibTex styled reference from a given PMID from the PubMed database 
    using PubMed's API
    '''
    # Get the reference from PubMed
    reference = getReference(id)
    # I the reference is not found
    if 'status' in reference[1].keys() and reference[1]['status'] == 'error' :
        output = 'Reference not found'
    else:
        output = formatReference(reference, use_short)

    click.echo(output)
    return

def saveReference(id, path, use_short):
    '''
    Append a reference to an bib file
    '''
    reference = getReference(id)
    if 'status' in reference[1].keys() and reference[1]['status'] == 'error' :
        click.echo("Reference not found")
        return
    with open(path, "a") as f:
        f.write(formatReference(reference, use_short))
    return
        

def convertReferences(input_file, output_file, use_short=False):
    '''Process a file of PMIDs or keyword queries (one per line), auto-detected.
    Lines of digits are treated as PMIDs; anything else as a keyword query.
    Output goes to output_file if given, otherwise stdout.
    '''
    successNumber, failNumber = 0, 0
    out = open(output_file, 'w') if output_file else None
    with open(input_file) as ih:
        for line in ih:
            entry = line.rstrip()
            if not entry or entry.startswith('#'):
                continue
            if entry.isdigit():
                ref = getReference(entry)
                if 'status' in ref[1] and ref[1]['status'] == 'error':
                    print(f'PMID {entry} NOT FOUND')
                    failNumber += 1
                    continue
                refs = [ref]
            else:
                refs = searchAndRank(entry, top_k=1)
                if not refs:
                    print(f'NOT FOUND: {entry}')
                    failNumber += 1
                    continue
            for ref in refs:
                bibtex = formatReference(ref, use_short)
                if out:
                    out.write(bibtex + '\n')
                else:
                    click.echo(bibtex)
                successNumber += 1
    if out:
        out.close()
    print(f'{successNumber} reference{"s" if successNumber > 1 else ""} retrieved, '
          f'{failNumber} not found.')

@click.command()
@click.option('-i','--id', default=None, help="The PubMed PMID.")
@click.option('-q','--query', default=None, help='Keyword search, e.g. "segmental duplications eichler"')
@click.option('--input-file', default=None, help='A text file with list of PMIDs or keyword queries (one per line)')
@click.option('--output-file', default=None, help='The output file to store BibTex styled references')
@click.option('--short-journal/--long-journal', default=False, help="Use short journal name")
@click.option('-K', '--max-results', default=5, show_default=True, help='Number of top matches to return for keyword search')
@click.option('--rank/--no-rank', default=False, show_default=True, help='Re-rank results by keyword match score (slower)')
@click.option('--source', default='pubmed', show_default=True,
              type=click.Choice(['pubmed', 'biorxiv'], case_sensitive=False),
              help='Database to search')
def pubMed2BibTex(id, query, input_file, output_file, short_journal, max_results, rank, source):
    '''
    Retrieve article reference from PubMed in BibTex format.
    '''
    if id:
        if output_file:
            saveReference(id, output_file, short_journal)
        else:
            showReference(id, short_journal)
    elif query:
        if source == 'biorxiv':
            bibtex_list = searchBiorxiv(query, max_results, rank)
            if not bibtex_list:
                click.echo('No results found.')
                return
            for bibtex in bibtex_list:
                if output_file:
                    with open(output_file, 'a') as f:
                        f.write(bibtex)
                else:
                    click.echo(bibtex)
        else:
            if rank:
                refs = searchAndRank(query, max_results)
            else:
                pmids = searchPubMed(query, max_results)
                refs = []
                for pmid in pmids:
                    ref = getReference(pmid)
                    if 'status' not in ref[1] or ref[1]['status'] != 'error':
                        refs.append(ref)
            if not refs:
                click.echo('No results found.')
                return
            for ref in refs:
                bibtex = formatReference(ref, short_journal)
                if output_file:
                    with open(output_file, 'a') as f:
                        f.write(bibtex)
                else:
                    click.echo(bibtex)
    elif input_file:
        convertReferences(input_file, output_file, short_journal)
    else:
        click.echo(pubMed2BibTex.get_help(click.Context(pubMed2BibTex)))

if __name__ == '__main__':
    pubMed2BibTex()
