'use strict';

const ENGINE_FILE = 'sommelai_engine.py';
const PRICE_FILE = 'precos_referencia.csv';
const GUIDE_FILE = 'GUIA_SOMMELIER_DIDATICO.md';
const APP_DIR = '/home/pyodide/app';
const ENGINE_PY = `
import contextlib, io, json, sys
sys.path.insert(0, ${JSON.stringify(APP_DIR)})
import sommelai_engine as se

def consultar(args_json):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = se.main(json.loads(args_json) + ["--json-only"])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    if code != 0:
        erros = [l for l in err.getvalue().splitlines() if l.startswith("Erro:")]
        message = erros[-1].removeprefix("Erro:").strip() if erros else ""
        return json.dumps({"error": message or "A consulta falhou."}, ensure_ascii=False)
    return out.getvalue()

def catalogo():
    return json.dumps({
        "perfis": [
            {"nome": p.nome, "categoria": se.CATEGORIAS[p.categoria], "marca": p.marca, "estilo": p.estilo}
            for p in se.CATALOGO
        ],
        "pratos": [{"nome": p.nome, "momento": p.momento} for p in se.PRATOS],
        "prompt_rotulo": se.PROMPT_ROTULO,
        "versao": se.ENGINE_VERSION,
    }, ensure_ascii=False)
`;

const TIPOS = {
    prato: {
        rotulo: 'Prato do dia',
        placeholder: 'Ex.: carne de sol com macaxeira',
        exemplos: ['Picanha na brasa', 'Carne de sol com macaxeira', 'Moqueca', 'Ceviche', 'Queijo azul', 'Bolo de rolo', 'Coxinha'],
        foto: false,
    },
    rotulo: {
        rotulo: 'Rótulo, uva ou estilo',
        placeholder: 'Ex.: Lagavulin 16, Malbec, Cohiba Siglo VI',
        exemplos: ['Lagavulin 16', 'Dom Pérignon', 'Pitú', 'Malbec', 'Cohiba Siglo VI', 'Geisha'],
        foto: true,
    },
    paladar: {
        rotulo: 'Como você gosta?',
        placeholder: 'Ex.: doce, frutado e pouco amargo',
        exemplos: ['doce e frutado, pouco amargo', 'encorpado e defumado', 'leve, fresco e mineral', 'não gosto de forte, prefiro baunilha'],
        foto: false,
    },
    combo: {
        rotulo: 'Charuto, bebida ou café',
        placeholder: 'Ex.: Romeo y Julieta Churchill',
        exemplos: ['Cohiba Siglo VI', 'Romeo y Julieta Churchill', 'Charuto baiano', 'Porto Tawny', 'Bourbon'],
        foto: true,
    },
};
const TIPO_CURTO = { prato: 'Prato', rotulo: 'Rótulo', paladar: 'Paladar', combo: 'Combo' };
const CATEGORIAS = ['Vinho', 'Destilado', 'Charuto', 'Café'];
const AI_MODEL = 'gemini-flash-latest';
const AI_URL = 'https://generativelanguage.googleapis.com/v1beta/models';
const FOTO_MAX_LADO = 1600;
const RECENTES_MAX = 8;

const store = {
    get(key, fallback) {
        try {
            const value = JSON.parse(localStorage.getItem(key));
            return value ?? fallback;
        } catch {
            return fallback;
        }
    },
    set(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    },
};

const state = {
    tipo: 'prato',
    categoria: '',
    engine: null,
    catalogo: null,
    ultima: null,
    ocupado: false,
    manualCarregado: false,
    recentes: store.get('tmind_recentes', []),
    ia: { key: '', model: AI_MODEL, ...store.get('tmind_ia', {}) },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove('show'), 3200);
}

function banner(message) {
    const el = $('#banner');
    el.hidden = !message;
    el.textContent = message || '';
}

function ocupado(texto) {
    state.ocupado = Boolean(texto);
    $('#busy').textContent = texto || '';
    $('#busy').classList.toggle('on', state.ocupado);
    $('#consultar').disabled = state.ocupado;
}

/* ---------- motor ---------- */

function startEngine() {
    state.engine ??= (async () => {
        if (!window.loadPyodide) throw new Error('O motor precisa de internet na primeira abertura.');
        const texto = (arquivo, obrigatorio) => fetch(arquivo, { cache: 'no-cache' }).then((r) => {
            if (r.ok) return r.text();
            if (obrigatorio) throw new Error(`Não encontrei ${arquivo}.`);
            return null;
        });
        const [pyodide, fonte, precos] = await Promise.all([
            window.loadPyodide(),
            texto(ENGINE_FILE, true),
            texto(PRICE_FILE, false),
        ]);
        pyodide.FS.mkdirTree(APP_DIR);
        pyodide.FS.writeFile(`${APP_DIR}/${ENGINE_FILE}`, fonte);
        if (precos) pyodide.FS.writeFile(`${APP_DIR}/${PRICE_FILE}`, precos);
        pyodide.runPython(ENGINE_PY);
        return {
            consultar: pyodide.globals.get('consultar'),
            catalogo: JSON.parse(pyodide.globals.get('catalogo')()),
        };
    })().catch((error) => {
        state.engine = null;
        if (/failed to fetch|networkerror|load failed/i.test(error.message)) {
            throw new Error('Não consegui baixar o motor. Confira a internet e tente de novo.');
        }
        throw error;
    });
    return state.engine;
}

function argumentos({ tipo, categoria, entrada }) {
    const args = [`--tipo=${tipo}`, '--top=3'];
    if (categoria) args.push(`--categoria=${categoria}`);
    if (entrada) args.push(`--entrada=${entrada}`);
    return args;
}

async function consultar(pedido, extra = {}) {
    if (state.ocupado) return;
    $('#erro').textContent = '';
    ocupado('Consultando a adega…');
    if (!state.ultima) $('#resultado').innerHTML = '<div class="card"><p class="carregando">Abrindo a adega (o motor carrega uma vez só)…</p></div>';
    try {
        await prepararCatalogo();
        const engine = await startEngine();
        const dados = JSON.parse(engine.consultar(JSON.stringify(argumentos(pedido))));
        if (dados.error) {
            $('#erro').textContent = dados.error;
            if (!state.ultima) renderBoasVindas();
            return;
        }
        state.ultima = { pedido, dados };
        renderResultado(dados, extra);
        lembrar(pedido);
        atualizarUrl(pedido);
        $('#compartilhar').hidden = false;
        if (window.matchMedia('(max-width: 900px)').matches) $('#resultado').scrollIntoView({ behavior: 'smooth' });
    } catch (error) {
        $('#erro').textContent = error.message;
        if (!state.ultima) renderBoasVindas();
    } finally {
        ocupado('');
    }
}

/* ---------- formulário ---------- */

function definirTipo(tipo) {
    state.tipo = TIPOS[tipo] ? tipo : 'prato';
    const conf = TIPOS[state.tipo];
    $$('#types button').forEach((b) => b.classList.toggle('active', b.dataset.tipo === state.tipo));
    $('#entrada-label').textContent = conf.rotulo;
    $('#entrada').placeholder = conf.placeholder;
    $('#exemplos').innerHTML = conf.exemplos
        .map((e) => `<button type="button" class="chip" data-exemplo="${esc(e)}">${esc(e)}</button>`).join('');
    $('#foto-field').hidden = !conf.foto;
    atualizarFotoHint();
    atualizarSugestoes();
}

function definirCategoria(categoria) {
    state.categoria = CATEGORIAS.includes(categoria) ? categoria : '';
    $$('#categorias .chip').forEach((b) => b.classList.toggle('active', b.dataset.categoria === state.categoria));
    atualizarSugestoes();
}

function atualizarSugestoes() {
    const cat = state.catalogo;
    if (!cat) return;
    let nomes = [];
    if (state.tipo === 'prato') nomes = cat.pratos.map((p) => p.nome);
    else if (state.tipo !== 'paladar') {
        nomes = cat.perfis.filter((p) => !state.categoria || p.categoria === state.categoria).map((p) => p.nome);
    }
    $('#sugestoes').innerHTML = nomes.map((n) => `<option value="${esc(n)}"></option>`).join('');
}

function pedidoDoFormulario() {
    return { tipo: state.tipo, categoria: state.categoria, entrada: $('#entrada').value.trim() };
}

function preencher(pedido) {
    definirTipo(pedido.tipo);
    definirCategoria(pedido.categoria || '');
    $('#entrada').value = pedido.entrada || '';
}

function irPara(pedido) {
    preencher(pedido);
    abrirAba('consultar');
    consultar(pedido);
}

/* ---------- histórico local e link ---------- */

function lembrar(pedido) {
    if (!pedido.entrada) return;
    const chave = (p) => `${p.tipo}|${p.categoria}|${p.entrada.toLowerCase()}`;
    state.recentes = [pedido, ...state.recentes.filter((p) => chave(p) !== chave(pedido))].slice(0, RECENTES_MAX);
    store.set('tmind_recentes', state.recentes);
    renderRecentes();
}

function renderRecentes() {
    $('#recentes-card').hidden = !state.recentes.length;
    $('#recentes').innerHTML = state.recentes.map((p, i) => (
        `<button type="button" class="chip" data-recente="${i}">${esc(TIPO_CURTO[p.tipo] || '')}: ${esc(p.entrada)}</button>`
    )).join('');
}

function urlDaConsulta(pedido) {
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    url.searchParams.set('tipo', pedido.tipo);
    if (pedido.categoria) url.searchParams.set('categoria', pedido.categoria);
    if (pedido.entrada) url.searchParams.set('entrada', pedido.entrada);
    return url.toString();
}

function atualizarUrl(pedido) {
    history.replaceState(null, '', urlDaConsulta(pedido));
}

async function compartilhar() {
    if (!state.ultima) return;
    const url = urlDaConsulta(state.ultima.pedido);
    const titulo = `TerroirMind AI · ${state.ultima.dados.protagonista.nome}`;
    try {
        if (navigator.share) {
            await navigator.share({ title: titulo, url });
            return;
        }
        await navigator.clipboard.writeText(url);
        toast('Link copiado. É só colar para os amigos.');
    } catch (error) {
        if (error?.name !== 'AbortError') toast(url);
    }
}

function pedidoDaUrl() {
    const params = new URLSearchParams(window.location.search);
    const tipo = params.get('tipo');
    if (!tipo || !TIPOS[tipo]) return null;
    return { tipo, categoria: params.get('categoria') || '', entrada: (params.get('entrada') || '').slice(0, 200) };
}

/* ---------- foto do rótulo ---------- */

function atualizarFotoHint() {
    $('#foto-hint').textContent = state.ia.key
        ? 'A foto vai para o Gemini, que só transcreve o rótulo. O resto é com o TerroirMind.'
        : 'Para ler fotos, salve antes uma chave grátis do Gemini (quadro abaixo).';
    $('#ia-status').textContent = state.ia.key
        ? 'Chave salva neste navegador. Fotos de rótulo liberadas.'
        : 'Sem chave: as consultas por texto funcionam normalmente; só a foto precisa dela.';
}

function aiError(status, data) {
    const message = data?.error?.message || '';
    if (status === 400 && /api key/i.test(message)) return 'Chave inválida. Confira a chave em aistudio.google.com/apikey.';
    if (status === 403) return 'A chave não tem permissão para o Gemini. Gere uma nova no AI Studio.';
    if (status === 404) return `Modelo "${state.ia.model}" não encontrado.`;
    if (status === 429) return 'Limite grátis da chave atingido. Espere um minuto e tente de novo.';
    if (status >= 500) return 'O Gemini está instável agora. Tente de novo em instantes.';
    return message || `Erro ${status} no Gemini.`;
}

async function reduzirFoto(arquivo) {
    try {
        const bitmap = await createImageBitmap(arquivo);
        const escala = Math.min(1, FOTO_MAX_LADO / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(bitmap.width * escala);
        canvas.height = Math.round(bitmap.height * escala);
        canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
        if (blob) return blob;
    } catch {
        /* formato que o navegador não desenha (ex.: HEIC fora do Safari): vai o original */
    }
    return arquivo;
}

function paraBase64(blob) {
    return new Promise((resolve, reject) => {
        const leitor = new FileReader();
        leitor.onload = () => resolve(String(leitor.result).split(',')[1]);
        leitor.onerror = () => reject(new Error('Não consegui ler a foto.'));
        leitor.readAsDataURL(blob);
    });
}

async function lerRotulo(arquivo) {
    const engine = await startEngine();
    const imagem = await reduzirFoto(arquivo);
    const response = await fetch(`${AI_URL}/${encodeURIComponent(state.ia.model || AI_MODEL)}:generateContent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-goog-api-key': state.ia.key },
        body: JSON.stringify({
            contents: [{
                parts: [
                    { text: engine.catalogo.prompt_rotulo },
                    { inline_data: { mime_type: imagem.type || 'image/jpeg', data: await paraBase64(imagem) } },
                ],
            }],
        }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(aiError(response.status, data));
    const texto = (data.candidates?.[0]?.content?.parts || []).filter((p) => !p.thought).map((p) => p.text || '').join(' ');
    const limpo = texto.replace(/\s+/g, ' ').trim();
    if (!limpo) throw new Error('O Gemini não conseguiu ler o rótulo. Tente uma foto mais próxima e com luz.');
    return limpo.slice(0, 200);
}

async function fotoEscolhida(event) {
    const arquivo = event.target.files?.[0];
    event.target.value = '';
    if (!arquivo) return;
    if (!state.ia.key) {
        $('#erro').textContent = 'Para ler a foto, salve uma chave grátis do Gemini no quadro abaixo.';
        $('#ia-card').open = true;
        return;
    }
    $('#erro').textContent = '';
    ocupado('Lendo o rótulo…');
    let texto;
    try {
        texto = await lerRotulo(arquivo);
    } catch (error) {
        $('#erro').textContent = error.message;
        return;
    } finally {
        ocupado('');
    }
    $('#entrada').value = texto;
    consultar(pedidoDoFormulario(), { foto: texto });
}

/* ---------- resultado ---------- */

function classeVeredito(nota) {
    if (nota >= 85) return 'v4';
    if (nota >= 72) return 'v3';
    if (nota >= 58) return 'v2';
    return 'v1';
}

function nivel(n) {
    return `<span class="nivel" aria-label="${n} de 5">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</span>`;
}

function renderPares(lista, titulo, comAcao = true) {
    if (!lista?.length) return '';
    const itens = lista.map((h) => {
        let acao = '';
        if (comAcao) {
            const pedido = h.categoria === 'Prato'
                ? { tipo: 'prato', categoria: state.ultima.dados.protagonista.categoria, entrada: h.nome }
                : { tipo: h.categoria === 'Charuto' ? 'combo' : 'rotulo', categoria: h.categoria, entrada: h.nome };
            const rotulo = h.categoria === 'Prato' ? 'Harmonizar este prato' : 'Ver a ficha';
            acao = `<button type="button" class="btn consultar-par" data-ir='${esc(JSON.stringify(pedido))}'>${rotulo}</button>`;
        }
        return `<li class="par">
            <div class="par-head">
                <span class="nome">${esc(h.nome)}<small>${esc(h.categoria)}</small></span>
                <span class="pill ${classeVeredito(h.nota)}">${esc(h.veredito || '')}</span>
                <span class="nota">${h.nota}<small>/100</small></span>
            </div>
            <div class="barra"><span style="width:${Math.max(0, Math.min(100, h.nota))}%"></span></div>
            <ul>${h.motivos.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
            ${acao}
        </li>`;
    }).join('');
    return `<div class="card"><h2>${esc(titulo)}</h2><ul class="pares">${itens}</ul></div>`;
}

function renderResultado(d, extra) {
    const p = d.protagonista;
    const didatica = p.descricao_didatica;
    const ficha = [
        p.marca ? ['Marca', p.marca] : null,
        ['Origem', p.origem],
        ...Object.entries(p.ficha || {}),
    ].filter(Boolean);

    const leitura = [];
    if (extra.foto) leitura.push(`Lido da foto: <b>${esc(extra.foto)}</b>`);
    else leitura.push(`Você pediu: <b>${esc(d.identificacao.texto)}</b>`);
    leitura.push(`confiança ${esc(d.identificacao.confianca)}`);
    if (d.prato) leitura.push(`prato: <b>${esc(d.prato.nome)}</b>`);

    const eixos = didatica.eixos.map((e) => (
        `<div class="eixo"><dt>${esc(e.rotulo)}</dt><dd>${nivel(e.nivel)}${esc(e.explicacao)}</dd></div>`
    )).join('');
    const servico = Object.values(p.dica_servico).map((s) => (
        `<div><b>${esc(s.rotulo)}</b><p>${esc(s.texto)}</p></div>`
    )).join('');
    const ofertas = (d.onde_encontrar || []).map((item) => item.ofertas.map((o) => (
        `<li><b>${esc(item.nome)}</b>: ${esc(o.produto)} · ${esc(o.preco_formatado)} · ${esc(o.loja)}${o.cidade ? ` (${esc(o.cidade)})` : ''}
         · visto em ${esc(o.data.split('-').reverse().join('/'))}${o.conferir ? ` <span class="conferir">· confira, o preço tem ${o.dias} dias</span>` : ''}
         ${o.link ? ` · <a href="${esc(o.link)}" target="_blank" rel="noopener">ver na loja</a>` : ''}</li>`
    )).join('')).join('');

    $('#resultado').innerHTML = `
        <div class="card hero">
            <p class="eyebrow">${esc(d.consulta.tipo_consulta)}</p>
            <h1>${esc(p.nome)}</h1>
            <p class="estilo">${esc(p.categoria)} · ${esc(p.estilo)}</p>
            <p class="poesia">“${esc(didatica.poesia)}”</p>
            <dl>${ficha.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
        </div>
        <div class="card">
            <p class="leitura" style="margin:0">${leitura.join(' · ')}</p>
            ${d.preferencias ? `<p class="leitura" style="margin:6px 0 0">Entendi do seu paladar: ${esc(d.preferencias.termos_reconhecidos.join(', ') || 'nada específico')}</p>` : ''}
            ${d.identificacao.observacao ? `<div class="nota-ident">${esc(d.identificacao.observacao)}</div>` : ''}
        </div>
        ${renderPares(d.harmonizacao.sugestoes, d.harmonizacao.titulo)}
        ${renderPares(d.alternativas, 'Afinidade com o seu paladar')}
        <div class="card">
            <h2>Descrição didática</h2>
            <dl class="eixos">
                <div class="eixo"><dt>Aromas</dt><dd>${esc(didatica.aromas.join(', '))}</dd></div>
                <div class="eixo"><dt>Ataque</dt><dd>${esc(didatica.ataque)}</dd></div>
                ${eixos}
                <div class="eixo"><dt>Retrossabor</dt><dd>${nivel(didatica.retrossabor.persistencia)}${esc(didatica.retrossabor.descricao)} ${esc(didatica.retrossabor.explicacao)}</dd></div>
            </dl>
        </div>
        <div class="card">
            <h2>Dica de serviço</h2>
            <div class="servico">${servico}</div>
            ${p.classicos?.length ? `<p class="small muted" style="margin:12px 0 0">Clássicos: ${esc(p.classicos.join(', '))}</p>` : ''}
        </div>
        ${ofertas ? `<div class="card"><h2>Onde encontrar · preço de referência</h2><ul class="ofertas">${ofertas}</ul>
            <p class="small muted" style="margin:10px 0 0">Preços anotados à mão; podem ter mudado. Confirme na loja.</p></div>` : ''}
    `;
}

function renderBoasVindas() {
    $('#resultado').innerHTML = `
        <div class="card welcome">
            <p class="eyebrow">Sommelier de bolso</p>
            <h1>Diga o prato, o rótulo ou o seu gosto.</h1>
            <p>O TerroirMind identifica, harmoniza e, acima de tudo, explica o porquê. Escolha um dos exemplos ao lado para começar.</p>
        </div>`;
}

/* ---------- catálogo e manual ---------- */

function renderCatalogo() {
    const cat = state.catalogo;
    const grupos = CATEGORIAS.map((categoria) => {
        const itens = cat.perfis.filter((p) => p.categoria === categoria).map((p) => {
            const pedido = { tipo: categoria === 'Charuto' ? 'combo' : 'rotulo', categoria, entrada: p.nome };
            return `<li><button type="button" data-ir='${esc(JSON.stringify(pedido))}'><span>${esc(p.nome)}</span><small>${esc(p.marca || 'estilo')}</small></button></li>`;
        }).join('');
        return `<div class="card"><h2>${esc(categoria)}</h2><ul>${itens}</ul></div>`;
    });
    const pratos = cat.pratos.map((p) => {
        const pedido = { tipo: 'prato', categoria: '', entrada: p.nome };
        return `<li><button type="button" data-ir='${esc(JSON.stringify(pedido))}'><span>${esc(p.nome)}</span><small>${esc(p.momento === 'manha' ? 'manhã' : p.momento)}</small></button></li>`;
    }).join('');
    grupos.push(`<div class="card"><h2>Pratos</h2><ul>${pratos}</ul></div>`);
    $('#catalogo').innerHTML = grupos.join('');
}

function slug(texto) {
    return texto.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s/g, '-');
}

async function carregarManual() {
    if (state.manualCarregado) return;
    state.manualCarregado = true;
    try {
        const texto = await (await fetch(GUIDE_FILE, { cache: 'no-cache' })).text();
        const alvo = $('#manual');
        alvo.innerHTML = window.marked ? window.marked.parse(texto) : `<pre>${esc(texto)}</pre>`;
        $$('h1, h2, h3', alvo).forEach((h) => {
            h.id = slug(h.textContent);
            h.style.scrollMarginTop = '120px';
        });
    } catch {
        state.manualCarregado = false;
        $('#manual').innerHTML = `<p class="muted">Não consegui abrir ${GUIDE_FILE}.</p>`;
    }
}

function abrirAba(nome) {
    $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === nome));
    $$('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${nome}`));
    if (nome === 'manual') carregarManual();
    window.scrollTo({ top: 0 });
}

/* ---------- eventos ---------- */

function ligarEventos() {
    $('#tabs').addEventListener('click', (e) => {
        const botao = e.target.closest('button[data-tab]');
        if (botao) abrirAba(botao.dataset.tab);
    });
    $('#types').addEventListener('click', (e) => {
        const botao = e.target.closest('button[data-tipo]');
        if (!botao) return;
        definirTipo(botao.dataset.tipo);
        $('#entrada').value = '';
        $('#erro').textContent = '';
        $('#entrada').focus();
    });
    $('#categorias').addEventListener('click', (e) => {
        const botao = e.target.closest('button[data-categoria]');
        if (botao) definirCategoria(botao.dataset.categoria);
    });
    $('#exemplos').addEventListener('click', (e) => {
        const botao = e.target.closest('button[data-exemplo]');
        if (!botao) return;
        $('#entrada').value = botao.dataset.exemplo;
        consultar(pedidoDoFormulario());
    });
    $('#form').addEventListener('submit', (e) => {
        e.preventDefault();
        const pedido = pedidoDoFormulario();
        if (!pedido.entrada) {
            $('#erro').textContent = 'Escreva o que você quer consultar ou toque em um exemplo.';
            return;
        }
        consultar(pedido);
    });
    $('#foto').addEventListener('change', fotoEscolhida);
    $('#compartilhar').addEventListener('click', compartilhar);
    $('#recentes').addEventListener('click', (e) => {
        const botao = e.target.closest('button[data-recente]');
        if (botao) irPara(state.recentes[Number(botao.dataset.recente)]);
    });
    document.addEventListener('click', (e) => {
        const botao = e.target.closest('[data-ir]');
        if (botao) irPara(JSON.parse(botao.dataset.ir));
    });
    $('#ia-chave').value = state.ia.key;
    $('#ia-salvar').addEventListener('click', () => {
        state.ia.key = $('#ia-chave').value.trim();
        store.set('tmind_ia', state.ia);
        atualizarFotoHint();
        toast(state.ia.key ? 'Chave salva neste navegador.' : 'Chave vazia.');
    });
    $('#ia-apagar').addEventListener('click', () => {
        state.ia.key = '';
        $('#ia-chave').value = '';
        store.set('tmind_ia', state.ia);
        atualizarFotoHint();
        toast('Chave apagada deste navegador.');
    });
}

async function iniciar() {
    ligarEventos();
    const pedido = pedidoDaUrl();
    if (pedido) preencher(pedido);
    else definirTipo('prato');
    renderRecentes();
    ocupado('Abrindo a adega…');
    try {
        await prepararCatalogo();
    } catch (error) {
        banner(`${error.message} Ao consultar, o TerroirMind tenta de novo.`);
        $('#catalogo').innerHTML = '<p class="muted">O catálogo depende do motor, que ainda não abriu.</p>';
        return;
    } finally {
        ocupado('');
    }
    if (pedido?.entrada) consultar(pedido);
}

async function prepararCatalogo() {
    const engine = await startEngine();
    if (state.catalogo) return;
    state.catalogo = engine.catalogo;
    banner('');
    atualizarSugestoes();
    renderCatalogo();
    const status = $('#motor-status');
    if (status) status.textContent = `Motor pronto (versão ${engine.catalogo.versao}). Escolha um exemplo ou escreva o seu.`;
}

iniciar();
