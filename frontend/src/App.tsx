import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";

type Lang = "it" | "en";
type Theme = "classic" | "tahoe";
type PlayerCount = 1 | 2 | 3 | 4;
type QuizKind = "fifa" | "mondo";
const COLOR_CSS: Record<string, string> = {
  WHITE: "#ffffff",
  RED: "#d11141",
  BLUE: "#0d47a1",
  GREEN: "#0a8a3e",
  YELLOW: "#ffd700",
  BLACK: "#141414",
  ORANGE: "#ff8c00",
  PURPLE: "#7b1fa2",
  BROWN: "#8b5a2b",
  GREY: "#9e9e9e",
  PINK: "#ffb6c1",
};
const TARGET_IMAGE_TYPES = ["flag_to_dominant_color", "flag_to_colors", "flag_to_color_count"];
type OptionKind = "flag" | "color" | "color_list" | "number";
type Option = {
  code: string;
  name: string;
  asset_url: string | null;
  continent?: string;
  kind?: OptionKind;
  colors?: string[];
};
type Question = {
  id: string;
  type: string;
  difficulty: number;
  language: string;
  target: Option;
  options: Option[];
  correct_index: number;
  prompt?: string;
  prompt_colors?: { key: string; name: string }[];
};
type DifficultyConfig = {
  level: number;
  questions: number;
  flag_to_name_weight: number;
  min_similarity: number;
  max_similarity: number;
  confederations: string[];
};
type PlayerResult = {
  nickname: string;
  flag?: string;
  score: number;
  total: number;
};
type Player = {
  nickname: string;
  flag: string;
};
type GameResult = {
  players: PlayerResult[];
  language: Lang;
  difficulty: number;
  scope: "fifa" | "world";
};
type ScoreEntry = {
  id: string;
  nickname: string;
  flag?: string;
  language: string;
  difficulty: number;
  score: number;
  total: number;
  accuracy: number;
  ts: string;
  rank?: number;
};
type CountryDetail = {
  full_name: string | null;
  official_name: string | null;
  capital: string | null;
  official_languages: string[];
  other_languages: string[];
  government_form: string | null;
  president: string | null;
  proclamation: string | null;
  area_km2: number | null;
  population: number | null;
  density: number | null;
  demonym: string | null;
  continent: string | null;
  borders: { code: string; name: string; asset_url: string }[];
  football: { wc: number; trophies: number; best: string };
};
type FlagDetailPayload = {
  code: string;
  name: string;
  name_en: string;
  confederation: string;
  asset_url: string;
  details: CountryDetail;
};
type WarmupStatus = {
  running: boolean;
  force: boolean;
  total: number;
  done: number;
  errors: number;
  started_at: string | null;
  finished_at: string | null;
};
type FlagView = { code: string; views: number; name: string; asset_url: string | null };
type TopScorer = {
  rank: number;
  player: string;
  team: string;
  code: string | null;
  name_it: string | null;
  asset_url: string | null;
  goals: number | null;
  games: number | null;
};

const NICK_KEY = "flagquiz.players";
const THEME_KEY = "flagquiz.theme";

const TIME_LIMITS: Record<number, number> = {
  1: 15,
  2: 15,
  3: 13,
  4: 11,
  5: 9,
  6: 7,
  7: 5,
};

const T = {
  it: {
    appTitle: "QuiZ Bandiere",
    tagline: "Quanto conosci le bandiere?",
    kindTitle: "Che quiz vuoi giocare?",
    kindHint: "Scegli tra le bandiere delle nazionali di calcio o quelle di tutto il mondo.",
    kindFifa: "Quiz Bandiere FIFA",
    kindFifaDesc: "Le bandiere delle 211 nazionali di calcio",
    kindMondo: "Quiz Bandiere Mondo",
    kindMondoDesc: "Tutte le bandiere del mondo (306)",
    modeTitle: "Quanti giocatori?",
    modeHint: "Scegli se giocare da solo o sfidare un amico.",
    onePlayer: "1 giocatore",
    onePlayerDesc: "Partita singola con punteggio personale.",
    twoPlayers: "2 giocatori",
    twoPlayersDesc: "Domande alternate tra i due giocatori.",
    threePlayers: "3 giocatori",
    threePlayersDesc: "Domande a rotazione tra tre giocatori.",
    fourPlayers: "4 giocatori",
    fourPlayersDesc: "Quattro amici, partita a quiz.",
    reset: "ReStart",
    pause: "Pausa",
    resume: "Riprendi",
    paused: "Gioco in pausa",
    pausedHint: "La domanda riprenderà da dove era stata interrotta.",
    nickTitle: (n: number) => (n > 1 ? "Inserisci i nickname" : "Inserisci il tuo nickname"),
    nickHint: "I nickname servono per registrare i punteggi in classifica.",
    nickPlayer: "Nickname giocatore {n}",
    chooseFlag: "Scegli la bandiera del tuo paese",
    chooseFlagHint: "La bandiera apparirà accanto al tuo nome in classifica.",
    flagRequired: "Scegli la bandiera per continuare",
    nickSubmit: "Continua",
    nickSavedAs: "Stai giocando come {name}",
    changePlayer: "Cambia giocatore",
    newPlayer: "Nuovo giocatore",
    chooseLang: "Scegli la lingua",
    chooseDiff: "Scegli il livello di difficoltà",
    themeToTahoe: "Tahoe",
    themeToClassic: "Classic",
    themeAriaTahoe: "Passa alla grafica macOS Tahoe",
    themeAriaClassic: "Torna alla grafica classica",
    langHint: "La tua scelta non cambia le bandiere, solo i testi del gioco.",
    diffHint: "Più alto è il livello, più domande e più difficili saranno i distrattori.",
    questions: (n: number) => `${n} domande`,
    levelNames: ["Principiante", "Base", "Intermedio", "Medio-avanzato", "Avanzato", "Esperto", "Maestro"],
    levelDescs: [
      "Mix: più «bandiera → nome» che «nome → bandiera», paesi UEFA e CONMEBOL, distrattori molto diversi.",
      "Mix con più «bandiera → nome», entra il CONCACAF, distrattori un po' più simili.",
      "Mix bilanciato con i primi «nome → bandiera», entra l'AFC, distrattori simili.",
      "Mix bilanciato, distrattori simili, entrano CAF e OFC.",
      "Mix con più «nome → bandiera», distrattori molto simili, tutte le confederazioni.",
      "Mix con molti «nome → bandiera», distrattori quasi identici, confederazioni difficili.",
      "Mix: più «nome → bandiera» che «bandiera → nome», i distrattori più simili in assoluto.",
    ],
    flagToName: "Di quale paese è questa bandiera?",
    nameToFlag: "Qual è la bandiera di {name}?",
    correct: "Corretto!",
    wrong: "Sbagliato — era {name}",
    timeUp: "Tempo scaduto — era {name}",
    turn: "Tocca a {name}",
    progress: "Domanda {n} di {total}",
    scoreLabel: "Punti",
    summaryTitle: "Partita terminata!",
    summaryScore: "Punteggio",
    of: (s: number, t: number) => `${s} su ${t}`,
    accuracy: "Precisione",
    playerLabel: "Giocatore {n}",
    playAgain: "Rigioca",
    menu: "Menu principale",
    changeLang: "Cambia lingua",
    gallery: "Galleria Bandiere delle Squadre Nazionali FIFA",
    galleryNav: "Galleria bandiere",
    searchFlags: "Cerca una bandiera per nome…",
    noResults: "Nessuna bandiera trovata per «{q}».",
    loading: "Caricamento…",
    error: "Errore:",
    backToMenu: "Torna al menu",
    leaderboard: "Classifica Migliori Giocatori · FIFA",
    leaderboardWorld: "Classifica Migliori Giocatori · Mondo",
    lbLevel: "Liv.",
    lbRank: "#",
    lbPlayer: "Giocatore",
    lbScore: "Punti",
    lbEmpty: "Nessun punteggio registrato. Gioca per comparire qui!",
    lbFlags: "Bandiere più viste nel quiz FIFA",
    lbNation: "Nazione",
    lbViews: "Visualizzazioni",
    lbFlagEmpty: "Nessuna visualizzazione registrata. Gioca per compilare questa classifica!",
    lbScorers: "Migliori marcatori di sempre",
    lbScorerPlayer: "Giocatore",
    lbScorerTeam: "Nazionale",
    lbScorerGoals: "Reti",
    lbScorerEmpty: "Classifica marcatori non disponibile.",
    saving: "Salvataggio punteggio…",
    rankChip: "Posizione #{rank}",
    savedOk: "Punteggio salvato!",
    savedFail: "Salvataggio non riuscito.",
    detail: {
      title: "Scheda bandiera",
      selectHint: "Clicca su una bandiera per vedere le informazioni sul paese.",
      loading: "Caricamento informazioni…",
      error: "Impossibile caricare le informazioni.",
      fullName: "Nome completo",
      officialName: "Nome ufficiale",
      officialLangs: "Lingue ufficiali",
      otherLangs: "Altre lingue",
      capital: "Capitale",
      government: "Forma di governo",
      president: "Presidente attuale",
      proclamation: "Proclamazione",
      area: "Superficie",
      population: "Popolazione attuale",
      density: "Densità",
      demonym: "Nome degli abitanti",
      continent: "Continente",
      borders: "Confini",
      bordersNone: "Nessun confine terrestre",
      wcPlayed: "Mondiali disputati",
      trophies: "Trofei calcistici",
      bestResults: "Migliori posizionamenti",
      km2: "km²",
      inhabitants: "abitanti",
      perKm2: "ab./km²",
    },
    warmup: {
      running: "Aggiornamento informazioni bandiere: {done}/{total}…",
      done: "Informazioni bandiere aggiornate ({total})",
      refresh: "Aggiorna",
      hint: "Le informazioni vengono aggiornate automaticamente ogni giorno.",
      error: "Aggiornamento non riuscito.",
      cacheSize: "Cache: {size}",
      cacheEmpty: "Cache vuota",
      clear: "Svuota cache",
      clearing: "Svuotamento…",
      cleared: "Cache svuotata",
      clearFail: "Svuotamento non riuscito.",
    },
    continents: {
      all: "Tutti i continenti",
      Europe: "Europa",
      Asia: "Asia",
      Africa: "Africa",
      Oceania: "Oceania",
      "North America": "America del Nord",
      "South America": "America del Sud",
    },
  },
  en: {
    appTitle: "Flag Quiz",
    tagline: "How well do you know flags?",
    kindTitle: "Which quiz do you want to play?",
    kindHint: "Pick between the national-team flags or the flags of the whole world.",
    kindFifa: "FIFA Flags Quiz",
    kindFifaDesc: "The flags of the 211 football national teams",
    kindMondo: "World Flags Quiz",
    kindMondoDesc: "Every flag in the world (306)",
    modeTitle: "How many players?",
    modeHint: "Play alone or challenge a friend.",
    onePlayer: "1 player",
    onePlayerDesc: "Single game with a personal score.",
    twoPlayers: "2 players",
    twoPlayersDesc: "Questions alternate between the two players.",
    threePlayers: "3 players",
    threePlayersDesc: "Questions rotate among three players.",
    fourPlayers: "4 players",
    fourPlayersDesc: "Four friends in a party quiz.",
    reset: "ReStart",
    pause: "Pause",
    resume: "Resume",
    paused: "Game paused",
    pausedHint: "The question will resume where it left off.",
    nickTitle: (n: number) => (n > 1 ? "Enter the nicknames" : "Enter your nickname"),
    nickHint: "Nicknames are used to record your scores in the leaderboard.",
    nickPlayer: "Player {n} nickname",
    chooseFlag: "Choose the flag of your country",
    chooseFlagHint: "The flag will appear next to your name on the leaderboard.",
    flagRequired: "Choose a flag to continue",
    nickSubmit: "Continue",
    nickSavedAs: "You are playing as {name}",
    changePlayer: "Change player",
    newPlayer: "New player",
    chooseLang: "Choose your language",
    chooseDiff: "Choose the difficulty level",
    themeToTahoe: "Tahoe",
    themeToClassic: "Classic",
    themeAriaTahoe: "Switch to the macOS Tahoe theme",
    themeAriaClassic: "Back to the classic theme",
    langHint: "Your choice only changes the game texts, not the flags.",
    diffHint: "Higher levels mean more questions and harder distractors.",
    questions: (n: number) => `${n} questions`,
    levelNames: ["Beginner", "Basic", "Intermediate", "Advanced", "Expert", "Master", "Grandmaster"],
    levelDescs: [
      "Mix: more «flag → name» than «name → flag», UEFA and CONMEBOL countries, very different distractors.",
      "Mix with more «flag → name», CONCACAF added, slightly similar distractors.",
      "Balanced mix with the first «name → flag» questions, AFC added, similar distractors.",
      "Balanced mix, similar distractors, CAF and OFC added.",
      "Mix with more «name → flag», very similar distractors, all confederations.",
      "Mix with many «name → flag», near-identical distractors, hard confederations.",
      "Mix: more «name → flag» than «flag → name», the most similar distractors there are.",
    ],
    flagToName: "Which country does this flag belong to?",
    nameToFlag: "Which is the flag of {name}?",
    correct: "Correct!",
    wrong: "Wrong — it was {name}",
    timeUp: "Time's up — it was {name}",
    turn: "{name}'s turn",
    progress: "Question {n} of {total}",
    scoreLabel: "Score",
    summaryTitle: "Game over!",
    summaryScore: "Score",
    of: (s: number, t: number) => `${s} out of ${t}`,
    accuracy: "Accuracy",
    playerLabel: "Player {n}",
    playAgain: "Play again",
    menu: "Main menu",
    changeLang: "Change language",
    gallery: "Flags gallery of the FIFA National Teams",
    galleryNav: "Flags gallery",
    searchFlags: "Search a flag by name…",
    noResults: "No flag found for «{q}».",
    loading: "Loading…",
    error: "Error:",
    backToMenu: "Back to menu",
    leaderboard: "Best scores · FIFA",
    leaderboardWorld: "Best scores · World",
    lbLevel: "Lv.",
    lbRank: "#",
    lbPlayer: "Player",
    lbScore: "Score",
    lbEmpty: "No scores yet. Play a game to show up here!",
    lbFlags: "Most viewed flags in the FIFA quiz",
    lbNation: "Country",
    lbViews: "Views",
    lbFlagEmpty: "No views yet. Play a game to fill this ranking!",
    lbScorers: "All-time top scorers",
    lbScorerPlayer: "Player",
    lbScorerTeam: "National team",
    lbScorerGoals: "Goals",
    lbScorerEmpty: "Top scorers ranking unavailable.",
    saving: "Saving score…",
    rankChip: "Position #{rank}",
    savedOk: "Score saved!",
    savedFail: "Could not save the score.",
    detail: {
      title: "Flag details",
      selectHint: "Click a flag to see country information.",
      loading: "Loading information…",
      error: "Could not load the information.",
      fullName: "Full name",
      officialName: "Official name",
      officialLangs: "Official languages",
      otherLangs: "Other languages",
      capital: "Capital",
      government: "Form of government",
      president: "Current president",
      proclamation: "Proclamation",
      area: "Area",
      population: "Current population",
      density: "Density",
      demonym: "Demonym",
      continent: "Continent",
      borders: "Borders",
      bordersNone: "No land borders",
      wcPlayed: "World Cups played",
      trophies: "Football trophies",
      bestResults: "Best results",
      km2: "km²",
      inhabitants: "inhabitants",
      perKm2: "per km²",
    },
    warmup: {
      running: "Updating flag information: {done}/{total}…",
      done: "Flag information updated ({total})",
      refresh: "Refresh",
      hint: "Information is refreshed automatically every day.",
      error: "Refresh failed.",
      cacheSize: "Cache: {size}",
      cacheEmpty: "Empty cache",
      clear: "Clear cache",
      clearing: "Clearing…",
      cleared: "Cache cleared",
      clearFail: "Could not clear the cache.",
    },
    continents: {
      all: "All continents",
      Europe: "Europe",
      Asia: "Asia",
      Africa: "Africa",
      Oceania: "Oceania",
      "North America": "North America",
      "South America": "South America",
    },
  },
};

function t(lang: Lang) {
  return T[lang];
}

function App() {
  const navigate = useNavigate();
  const [players, setPlayers] = useState<Player[]>(() => {
    try {
      const raw = localStorage.getItem(NICK_KEY);
      if (!raw) return [];
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const valid = parsed
          .map((v): Player | null => {
            if (typeof v === "string" && v.length > 0) return { nickname: v, flag: "" };
            if (v && typeof v === "object") {
              const o = v as Record<string, unknown>;
              if (typeof o.nickname === "string" && o.nickname.length > 0) {
                return {
                  nickname: o.nickname,
                  flag: typeof o.flag === "string" ? o.flag : "",
                };
              }
            }
            return null;
          })
          .filter((p): p is Player => p !== null);
        if (valid.length >= 1 && valid.length <= 4) return valid;
      }
      return [];
    } catch {
      return [];
    }
  });
  const [language, setLanguage] = useState<Lang | null>(null);
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return localStorage.getItem(THEME_KEY) === "tahoe" ? "tahoe" : "classic";
    } catch {
      return "classic";
    }
  });
  const [difficulty, setDifficulty] = useState<DifficultyConfig | null>(null);
  const [kind, setKind] = useState<QuizKind | null>(null);
  const [screen, setScreen] = useState<
    "kind" | "mode" | "nick" | "lang" | "diff" | "quiz" | "summary"
  >("kind");
  const [result, setResult] = useState<GameResult | null>(null);
  const [gameId, setGameId] = useState(0);
  const [paused, setPaused] = useState(false);
  const [finishedGames, setFinishedGames] = useState(0);
  const [selectedFlag, setSelectedFlag] = useState<string | null>(null);
  const [warmup, setWarmup] = useState<WarmupStatus | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const location = useLocation();
  const onFlagsPage = location.pathname === "/flags";

  const idleWarmup: WarmupStatus = {
    running: false,
    force: false,
    total: 0,
    done: 0,
    errors: 0,
    started_at: null,
    finished_at: null,
  };

  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore storage errors */
    }
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!onFlagsPage) return;
    fetch("/api/flags/warmup", { method: "POST" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("warmup start failed"))))
      .then(setWarmup)
      .catch(() => setWarmup(idleWarmup));
  }, [onFlagsPage]);

  useEffect(() => {
    if (!onFlagsPage || !warmup?.running) return;
    const id = window.setTimeout(() => {
      fetch("/api/flags/warmup/status")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("status failed"))))
        .then(setWarmup)
        .catch(() => {});
    }, 1500);
    return () => window.clearTimeout(id);
  }, [onFlagsPage, warmup?.running]);

  const refreshFlagInfo = () => {
    setRefreshToken((t) => t + 1);
    fetch("/api/flags/warmup?force=true", { method: "POST" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("refresh start failed"))))
      .then(setWarmup)
      .catch(() => {});
  };

  const chooseKind = (chosen: QuizKind) => {
    setKind(chosen);
    setScreen(players.length > 0 ? "lang" : "mode");
  };

  const chooseMode = (count: PlayerCount) => {
    setPlayers(Array.from({ length: count }, () => ({ nickname: "", flag: "" })));
    setPaused(false);
    setScreen("nick");
  };

  const saveNicknames = (playerSetups: Player[]) => {
    setPlayers(playerSetups);
    try {
      localStorage.setItem(NICK_KEY, JSON.stringify(playerSetups));
    } catch {
      /* ignore storage errors */
    }
    setScreen("lang");
  };

  const chooseLang = (lang: Lang) => {
    setLanguage(lang);
    setScreen("diff");
  };

  const startGame = (cfg: DifficultyConfig) => {
    setDifficulty(cfg);
    setGameId((g) => g + 1);
    setPaused(false);
    setScreen("quiz");
  };

  const finishGame = (playerResults: PlayerResult[]) => {
    if (!language || !difficulty || !kind) return;
    setResult({
      players: playerResults,
      language,
      difficulty: difficulty.level,
      scope: kind === "mondo" ? "world" : "fifa",
    });
    setScreen("summary");
  };

  const onScoresSaved = () => {
    setFinishedGames((g) => g + 1);
  };

  const replay = () => {
    if (!difficulty) return;
    setGameId((g) => g + 1);
    setPaused(false);
    setScreen("quiz");
  };

  const newPlayer = () => {
    try {
      localStorage.removeItem(NICK_KEY);
    } catch {
      /* ignore storage errors */
    }
    setPlayers([]);
    setKind(null);
    setLanguage(null);
    setDifficulty(null);
    setResult(null);
    setPaused(false);
    setScreen("kind");
  };

  const resetGame = () => {
    newPlayer();
    navigate("/");
  };

  return (
    <div className="layout">
      <div className="main">
        <nav>
          <Link to="/" className="brand">
            {language ? t(language).appTitle : "QuiZ Bandiere"}
          </Link>
          <div className="nav-actions">
            <Link to="/flags">{t(language ?? "it").galleryNav}</Link>
            <button
              type="button"
              className="nav-lang"
              onClick={() => setLanguage((language ?? "it") === "it" ? "en" : "it")}
              aria-label="Switch language / Cambia lingua"
            >
              {(language ?? "it") === "it" ? "EN" : "IT"}
            </button>
            <button
              type="button"
              className={"nav-theme" + (theme === "tahoe" ? " active" : "")}
              onClick={() => setTheme(theme === "classic" ? "tahoe" : "classic")}
              aria-label={
                theme === "classic"
                  ? t(language ?? "it").themeAriaTahoe
                  : t(language ?? "it").themeAriaClassic
              }
            >
              <svg className="nav-theme-icon" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d="M13.9 7.6 8.4 2.1a.7.7 0 0 0-1 0l-5.3 5.3a.7.7 0 0 0 0 1L7.5 13a.7.7 0 0 0 1 0l5.4-5.4a.7.7 0 0 0 0-1ZM7 13l-.3.3a1 1 0 0 1-1.4 0l-.6-.6a1 1 0 0 1 0-1.4L5 11v2h2Zm4.2-4.2H5.8l-2-2h8.4l-2 2Z"
                  fill="currentColor"
                />
              </svg>
              {theme === "classic" ? t(language ?? "it").themeToTahoe : t(language ?? "it").themeToClassic}
            </button>
            <img
              className="nav-lang-flag"
              src={`/api/flags/${(language ?? "it") === "it" ? "ITA" : "ENG"}/asset`}
              alt=""
            />
            <button className="nav-reset" onClick={resetGame}>
              {t(language ?? "it").reset}
            </button>
            {screen === "quiz" && (
              <button
                className={"nav-pause" + (paused ? " active" : "")}
                onClick={() => setPaused((p) => !p)}
              >
                {paused
                  ? t(language ?? "it").resume
                  : "⏸ " + t(language ?? "it").pause}
              </button>
            )}
          </div>
        </nav>
        {onFlagsPage && (
          <WarmupBar
            language={language ?? "it"}
            status={warmup}
            onRefresh={refreshFlagInfo}
            onCleared={() => setRefreshToken((t) => t + 1)}
          />
        )}
        <Routes>
          <Route
            path="/"
            element={
              !kind || screen === "kind" ? (
                <QuizKindScreen onChoose={chooseKind} />
              ) : screen === "mode" || players.length === 0 ? (
                <ModeScreen kind={kind} onChoose={chooseMode} />
              ) : screen === "nick" ? (
              <NicknameScreen
                count={players.length as PlayerCount}
                defaults={players}
                onDone={saveNicknames}
              />
              ) : screen === "lang" || !language ? (
                <LanguageScreen
                  players={players}
                  onChoose={chooseLang}
                  onChangePlayer={newPlayer}
                />
              ) : screen === "diff" || !difficulty ? (
                <DifficultyScreen
                  language={language}
                  onStart={startGame}
                  onChangeLang={() => setScreen("lang")}
                />
              ) : screen === "summary" && result ? (
                <SummaryScreen
                  result={result}
                  onReplay={replay}
                  onMenu={() => setScreen("diff")}
                  onNewPlayer={newPlayer}
                  onSaved={onScoresSaved}
                />
              ) : (
                <QuizScreen
                  key={gameId}
                  language={language}
                  difficulty={difficulty}
                  players={players}
                  scope={kind === "mondo" ? "world" : "fifa"}
                  paused={paused}
                  onFinish={finishGame}
                />
              )
            }
          />
          <Route
            path="/flags"
            element={
              <Gallery
                language={language ?? "it"}
                selected={selectedFlag}
                onSelect={setSelectedFlag}
              />
            }
          />
        </Routes>
      </div>
      <div className="side">
        <Leaderboard
          key={finishedGames}
          language={language ?? "it"}
          activePlayers={players.map((p) => p.nickname)}
        />
        {onFlagsPage && selectedFlag && (
          <FlagDetail
            code={selectedFlag}
            language={language ?? "it"}
            refreshToken={refreshToken}
            onSelect={setSelectedFlag}
          />
        )}
      </div>
    </div>
  );
}

function QuizKindScreen({ onChoose }: { onChoose: (kind: QuizKind) => void }) {
  const s = t("it");
  return (
    <div className="menu screen-enter">
      <div className="hero">
        <h1 className="app-title">QuiZ Bandiere</h1>
        <p className="tagline">{s.tagline}</p>
      </div>
      <h2 className="menu-title">{s.kindTitle}</h2>
      <p className="hint">{s.kindHint}</p>
      <div className="lang-buttons">
        <button className="btn-hero it" onClick={() => onChoose("fifa")}>
          <span className="flag-circle it-flag">⚽</span>
          <span className="btn-label">{s.kindFifa}</span>
          <span className="btn-desc">{s.kindFifaDesc}</span>
        </button>
        <button className="btn-hero en" onClick={() => onChoose("mondo")}>
          <span className="flag-circle en-flag">🌍</span>
          <span className="btn-label">{s.kindMondo}</span>
          <span className="btn-desc">{s.kindMondoDesc}</span>
        </button>
      </div>
    </div>
  );
}

function ModeScreen({
  kind,
  onChoose,
}: {
  kind: QuizKind;
  onChoose: (count: PlayerCount) => void;
}) {
  const s = t("it");
  const counts: PlayerCount[] = [1, 2, 3, 4];
  const label = (n: PlayerCount) =>
    n === 1 ? s.onePlayer : n === 2 ? s.twoPlayers : n === 3 ? s.threePlayers : s.fourPlayers;
  const desc = (n: PlayerCount) =>
    n === 1
      ? s.onePlayerDesc
      : n === 2
        ? s.twoPlayersDesc
        : n === 3
          ? s.threePlayersDesc
          : s.fourPlayersDesc;
  return (
    <div className="menu screen-enter">
      <div className="hero">
        <h1 className="app-title">{kind === "fifa" ? s.kindFifa : s.kindMondo}</h1>
      </div>
      <h2 className="menu-title">{s.modeTitle}</h2>
      <p className="hint">{s.modeHint}</p>
      <div className="count-grid">
        {counts.map((n) => (
          <button key={n} className="count-btn" onClick={() => onChoose(n)}>
            <span className="count-num">{n}</span>
            <span className="count-label">{label(n)}</span>
            <span className="btn-desc">{desc(n)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function NicknameScreen({
  count,
  defaults,
  onDone,
}: {
  count: PlayerCount;
  defaults: Player[];
  onDone: (players: Player[]) => void;
}) {
  const s = t("it");
  const [values, setValues] = useState<Player[]>(() =>
    defaults.length === count && defaults.every((p) => p.nickname)
      ? defaults.map((p) => ({ ...p }))
      : Array.from({ length: count }, () => ({ nickname: "", flag: "" }))
  );
  const [flags, setFlags] = useState<Record<string, Option> | null>(null);
  const [openFor, setOpenFor] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const canSubmit = values.every((p) => p.nickname.trim().length > 0 && p.flag.length > 0);

  useEffect(() => {
    fetch("/api/flags?language=it")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("flags fetch failed"))))
      .then((d) => setFlags(d.flags))
      .catch(() => setFlags({}));
  }, []);

  const setAt = (i: number, v: Player) => setValues((prev) => prev.map((p, j) => (j === i ? v : p)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canSubmit) onDone(values.map((p) => ({ nickname: p.nickname.trim(), flag: p.flag })));
  };

  const list = useMemo(() => {
    if (!flags) return [];
    const entries = Object.entries(flags);
    entries.sort((a, b) => a[1].name.localeCompare(b[1].name));
    return entries;
  }, [flags]);
  const term = query.trim().toLowerCase();
  const matches = term
    ? list.filter(([, f]) => f.name.toLowerCase().includes(term) || f.code.toLowerCase().includes(term))
    : list;

  return (
    <div className="menu screen-enter">
      <div className="hero">
        <h1 className="app-title">QuiZ Bandiere</h1>
      </div>
      <h2 className="menu-title">{s.nickTitle(count)}</h2>
      <p className="hint">{s.nickHint}</p>
      <form className="nick-form nick-form-col" onSubmit={submit}>
        {values.map((p, i) => (
          <div key={i} className="nick-player">
            <input
              type="text"
              className="nick-input"
              maxLength={24}
              placeholder={s.nickPlayer.replace("{n}", String(i + 1))}
              value={p.nickname}
              autoFocus={i === 0}
              onChange={(e) => setAt(i, { ...p, nickname: e.target.value })}
            />
            <div className="flag-pick">
              <button
                type="button"
                className={
                  "flag-pick-btn" +
                  (p.flag ? "" : " empty") +
                  (p.nickname.trim().length > 0 && !p.flag ? " needed" : "")
                }
                onClick={() => {
                  setOpenFor(openFor === i ? null : i);
                  setQuery("");
                }}
              >
                {p.flag ? (
                  <>
                    <img className="flag-pick-img" src={`/api/flags/${p.flag}/asset`} alt="" />
                    <span className="flag-pick-name">{flags?.[p.flag]?.name ?? p.flag}</span>
                  </>
                ) : (
                  <span className="flag-pick-placeholder">{s.chooseFlag}</span>
                )}
              </button>
              {openFor === i && (
                <div className="flag-pick-panel">
                  <input
                    type="search"
                    className="flag-pick-search"
                    placeholder={s.searchFlags}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <div className="flag-pick-list">
                    {matches.map(([code, f]) => (
                      <button
                        key={code}
                        type="button"
                        className={"flag-pick-item" + (code === p.flag ? " selected" : "")}
                        onClick={() => {
                          setAt(i, { ...p, flag: code });
                          setOpenFor(null);
                        }}
                      >
                        <img src={f.asset_url ?? ""} alt="" loading="lazy" decoding="async" />
                        <span>{f.name}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {p.nickname.trim().length > 0 && !p.flag && (
              <p className="flag-required">{s.flagRequired}</p>
            )}
          </div>
        ))}
        <p className="hint">{s.chooseFlagHint}</p>
        <button className="next" type="submit" disabled={!canSubmit}>
          {s.nickSubmit}
        </button>
      </form>
    </div>
  );
}

function LanguageScreen({
  players,
  onChoose,
  onChangePlayer,
}: {
  players: Player[];
  onChoose: (lang: Lang) => void;
  onChangePlayer: () => void;
}) {
  const s = t("it");
  const who = players.map((p) => p.nickname).join(" e ");
  return (
    <div className="menu screen-enter">
      <div className="hero">
        <h1 className="app-title">{s.appTitle}</h1>
        <p className="tagline">{s.tagline}</p>
      </div>
      <p className="hint">
        {s.nickSavedAs.replace("{name}", who)}{" "}
        <button className="link-btn" onClick={onChangePlayer}>
          {s.changePlayer}
        </button>
      </p>
      <h2 className="menu-title">{s.chooseLang}</h2>
      <p className="hint">{s.langHint}</p>
      <div className="lang-buttons">
        <button className="btn-hero it" onClick={() => onChoose("it")}>
          <span className="flag-circle it-flag">🇮🇹</span>
          <span className="btn-label">Italiano</span>
        </button>
        <button className="btn-hero en" onClick={() => onChoose("en")}>
          <span className="flag-circle en-flag">🇬🇧</span>
          <span className="btn-label">English</span>
        </button>
      </div>
    </div>
  );
}

function DifficultyScreen({
  language,
  onStart,
  onChangeLang,
}: {
  language: Lang;
  onStart: (cfg: DifficultyConfig) => void;
  onChangeLang: () => void;
}) {
  const s = t(language);
  const [config, setConfig] = useState<DifficultyConfig[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/quiz/config")
      .then((r) => {
        if (!r.ok) throw new Error("config fetch failed");
        return r.json();
      })
      .then((d) => setConfig(d.difficulty))
      .catch((e) => setError((e as Error).message));
  }, []);

  if (error) return <p className="error">Errore: {error}</p>;
  if (!config) return <p>{s.loading}</p>;

  return (
    <div className="menu screen-enter">
      <div className="menu-head">
        <h2 className="menu-title">{s.chooseDiff}</h2>
        <button className="link-btn" onClick={onChangeLang}>
          {s.changeLang}
        </button>
      </div>
      <p className="hint">{s.diffHint}</p>
      <div className="difficulty-list">
        {config.map((cfg, i) => {
          const name = s.levelNames[i];
          const desc = s.levelDescs[i];
          return (
            <button
              key={cfg.level}
              className="level-btn"
              style={{ "--lv": 24 + i * 18 } as CSSProperties}
              onClick={() => onStart(cfg)}
            >
              <span className="level-number">{cfg.level}</span>
              <span className="level-body">
                <span className="level-name">{name}</span>
                <span className="level-desc">{desc}</span>
                <span className="level-meta">{s.questions(cfg.questions)}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function QuizScreen({
  language,
  difficulty,
  players,
  scope,
  paused,
  onFinish,
}: {
  language: Lang;
  difficulty: DifficultyConfig;
  players: Player[];
  scope: "fifa" | "world";
  paused: boolean;
  onFinish: (players: PlayerResult[]) => void;
}) {
  const s = t(language);
  const timeLimit = TIME_LIMITS[difficulty.level] ?? 10;
  const total = difficulty.questions;
  const [question, setQuestion] = useState<Question | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [scores, setScores] = useState<number[]>(() => players.map(() => 0));
  const [answeredTotal, setAnsweredTotal] = useState(0);
  const [currentPlayer, setCurrentPlayer] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [timeLeft, setTimeLeft] = useState(timeLimit);
  const loading = useRef(false);
  const timeoutFired = useRef(false);
  const usedOptions = useRef<Set<string>>(new Set());
  const usedOptionSets = useRef<Set<string>>(new Set());
  const typeCount = useRef<Record<string, number>>({});

  const loadQuestion = useCallback(
    async (forceType?: "flag_to_name" | "name_to_flag") => {
      setSelected(null);
      // reset the countdown in the same commit: while the next question is
      // being fetched the previous, almost-expired timer must not fire
      setTimeLeft(TIME_LIMITS[difficulty.level] ?? 10);
      try {
        let q: Question | null = null;
        for (let attempt = 0; attempt < 8; attempt++) {
          const excl = Array.from(usedOptions.current);
          const url =
            `/api/quiz/question?scope=${scope}&difficulty=${difficulty.level}&language=${language}` +
            (forceType ? `&qtype=${forceType}` : "") +
            (excl.length ? `&exclude=${encodeURIComponent(excl.join(","))}` : "");
          const res = await fetch(url);
          if (!res.ok) throw new Error("question fetch failed");
          const next: Question = await res.json();
          if (forceType && next.type !== forceType) continue;
          const flagCodes = next.options
            .filter((o) => !o.kind || o.kind === "flag")
            .map((o) => o.code)
            .sort()
            .join("|");
          const sig = flagCodes.length ? flagCodes : next.type + "-" + next.target.code;
          if (usedOptionSets.current.has(sig)) continue;
          next.options.forEach((o) => {
            if (!o.kind || o.kind === "flag") usedOptions.current.add(o.code);
          });
          usedOptions.current.add(next.target.code);
          usedOptionSets.current.add(sig);
          typeCount.current[next.type] = (typeCount.current[next.type] ?? 0) + 1;
          q = next;
          break;
        }
        if (q) {
          timeoutFired.current = false;
          setQuestion(q);
          setTimeLeft(timeLimit);
        } else throw new Error("could not find a new question");
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [difficulty.level, language, scope]
  );

  useEffect(() => {
    if (!loading.current) {
      loading.current = true;
      loadQuestion();
    }
  }, [loadQuestion]);

  useEffect(() => {
    if (!question) return;
    setTimeLeft(timeLimit);
  }, [question, timeLimit]);

  useEffect(() => {
    if (!question || selected !== null || paused) return;
    const id = window.setInterval(() => {
      setTimeLeft((t) => (t > 0 ? t - 1 : t));
    }, 1000);
    return () => window.clearInterval(id);
  }, [question, selected, paused]);

  useEffect(() => {
    if (!question || selected !== null || paused) return;
    if (timeLeft <= 0 && !timeoutFired.current) {
      timeoutFired.current = true;
      setSelected(-1);
      setAnsweredTotal((a) => a + 1);
    }
  }, [timeLeft, question, selected, paused]);

  const finish = () => {
    const perPlayer: PlayerResult[] = players.map((p, i) => {
      const base = Math.floor(total / players.length);
      const extra = i < (total % players.length) ? 1 : 0;
      return { nickname: p.nickname, flag: p.flag, score: scores[i], total: base + extra };
    });
    onFinish(perPlayer);
  };

  const isLast = answeredTotal >= total;

  const next = () => {
    if (isLast) {
      finish();
    } else {
      if (players.length > 1) setCurrentPlayer((p) => (p + 1) % players.length);
      let forceType: "flag_to_name" | "name_to_flag" | undefined;
      if (answeredTotal + 1 >= total) {
        if (typeCount.current.flag_to_name === 0) forceType = "name_to_flag";
        else if (typeCount.current.name_to_flag === 0) forceType = "flag_to_name";
      }
      loadQuestion(forceType);
    }
  };

  useEffect(() => {
    if (selected === null || paused) return;
    const timer = window.setTimeout(next, 2000);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, paused]);

  if (error) return <p className="error">{s.error} {error}</p>;
  if (!question) return <p>{s.loading}</p>;

  const isCorrect = selected !== null && selected === question.correct_index;
  const timedOut = selected === -1;
  const showFlag = question.type === "flag_to_name";
  const showBigFlag = showFlag || TARGET_IMAGE_TYPES.includes(question.type);
  const showBigName = question.type === "name_to_flag";

  const choose = (index: number) => {
    if (selected !== null) return;
    if (index === question.correct_index) {
      setScores((prev) => prev.map((v, i) => (i === currentPlayer ? v + 1 : v)));
    }
    setSelected(index);
    setAnsweredTotal((a) => a + 1);
  };

  const progressPct = Math.round((answeredTotal / total) * 100);
  const active = players[currentPlayer].nickname;

  return (
    <div className="quiz screen-enter">
      <header className="scorebar">
        <span className="brand-mini">{s.appTitle}</span>
        {players.length > 1 ? (
          <div className="players-score">
            {players.map((p, i) => (
              <span key={`${p.nickname}-${i}`} className={"score-pill" + (i === currentPlayer ? " active" : "")}>
                {p.flag && <img className="pill-flag" src={`/api/flags/${p.flag}/asset`} alt="" />}
                {p.nickname}: {scores[i]}
              </span>
            ))}
          </div>
        ) : (
          <span className="score-pill">
            {players[0].flag && <img className="pill-flag" src={`/api/flags/${players[0].flag}/asset`} alt="" />}
            {active}: {scores[0]}
          </span>
        )}
      </header>

      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${progressPct}%` }} />
      </div>
      <p className="progress-label">
        {s.progress.replace("{n}", String(Math.min(answeredTotal + 1, total))).replace("{total}", String(total))}
      </p>

      {players.length > 1 && <p className="turn">{s.turn.replace("{name}", active)}</p>}

      {paused ? (
        <div className="pause-overlay">
          <span className="pause-icon">⏸</span>
          <p className="pause-title">{s.paused}</p>
          <p className="hint">{s.pausedHint}</p>
        </div>
      ) : (
        <>
          {selected === null && (
            <div className={"countdown" + (timeLeft <= Math.max(1, Math.ceil(timeLimit * 0.25)) ? " low" : "")}>
              <div className="countdown-track">
                <div
                  className="countdown-fill"
                  style={{ width: `${(timeLeft / timeLimit) * 100}%` }}
                />
              </div>
              <span className="countdown-num">{timeLeft}</span>
            </div>
          )}

          <p className="prompt">
            {question.prompt ? (
              <>
                {question.prompt}
                {question.prompt_colors && question.prompt_colors.length > 0 && (
                  <span className="prompt-chips">
                    {question.prompt_colors.map((c) => (
                      <span key={c.key} className="chip chip-lg" style={{ background: COLOR_CSS[c.key] ?? "#cccccc" }}>
                        {c.name}
                      </span>
                    ))}
                  </span>
                )}
              </>
            ) : showFlag ? (
              s.flagToName
            ) : (
              s.nameToFlag.replace("{name}", question.target.name)
            )}
          </p>

          {showBigFlag ? (
            <img
              src={question.target.asset_url ?? ""}
              alt="bandiera"
              className="big-flag"
            />
          ) : showBigName ? (
            <div className="big-flag big-name">{question.target.name}</div>
          ) : null}

          <div className="options">
            {question.options.map((opt, i) => {
              let cls = "option";
              if (opt.kind === "color") cls += " opt-color";
              if (opt.kind === "number") cls += " opt-number";
              if (selected !== null) {
                if (i === question.correct_index) cls += " correct";
                else if (i === selected) cls += " wrong";
                else cls += " dim";
              }
              return (
                <button
                  key={opt.code}
                  className={cls}
                  disabled={selected !== null}
                  onClick={() => choose(i)}
                >
                  {opt.kind === "color" ? (
                    <span className="opt-color-inner">
                      <span className="chip chip-md" style={{ background: COLOR_CSS[opt.code] ?? "#cccccc" }} />
                      <span className="opt-name">{opt.name}</span>
                    </span>
                  ) : opt.kind === "color_list" ? (
                    <span className="opt-name color-list-name">
                      <span className="chip-row">
                        {(opt.colors ?? []).map((c) => (
                          <span key={c} className="chip chip-sm" style={{ background: COLOR_CSS[c] ?? "#cccccc" }} />
                        ))}
                      </span>
                      <span>{opt.name}</span>
                    </span>
                  ) : opt.kind === "number" ? (
                    <span className="opt-number-name">{opt.name}</span>
                  ) : showFlag ? (
                    <span className="opt-name">{opt.name}</span>
                  ) : (
                    <img src={opt.asset_url ?? ""} alt={opt.name} />
                  )}
                </button>
              );
            })}
          </div>

          {selected !== null && (
            <div className="result">
              <p className={isCorrect ? "ok" : "no"}>
                {isCorrect
                  ? s.correct
                  : (timedOut ? s.timeUp : s.wrong).replace(
                      "{name}",
                      question.options[question.correct_index].name
                    )}
              </p>
              <div className="time-bar" aria-hidden="true">
                <div className="time-bar-fill" />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SummaryScreen({
  result,
  onReplay,
  onMenu,
  onNewPlayer,
  onSaved,
}: {
  result: GameResult;
  onReplay: () => void;
  onMenu: () => void;
  onNewPlayer: () => void;
  onSaved: () => void;
}) {
  const s = t(result.language);
  const [saved, setSaved] = useState<{ state: "saving" | "ok" | "fail"; ranks: (number | null)[] }>({
    state: "saving",
    ranks: [],
  });
  const submitted = useRef(false);

  useEffect(() => {
    if (submitted.current) return;
    submitted.current = true;
    let cancelled = false;
    Promise.all(
      result.players.map((p) =>
        fetch("/api/leaderboard", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nickname: p.nickname,
            language: result.language,
            difficulty: result.difficulty,
            score: p.score,
            total: p.total,
            flag: p.flag,
            scope: result.scope,
          }),
        })
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error("bad status"))))
          .then((d) => d.rank as number)
          .catch(() => null)
      )
    )
      .then((ranks) => {
        if (!cancelled) setSaved({ state: "ok", ranks });
        onSaved();
      })
      .catch(() => {
        if (!cancelled) setSaved({ state: "fail", ranks: [] });
        onSaved();
      });
    return () => {
      cancelled = true;
    };
  }, [result]);

  return (
    <div className="menu screen-enter">
      <h2 className="menu-title">{s.summaryTitle}</h2>
      <div className="players-grid">
        {result.players.map((p, i) => {
          const pct = Math.round((p.score / Math.max(p.total, 1)) * 100);
          const rank = saved.ranks[i] ?? null;
          return (
            <div key={i} className="player-card">
              <p className="player-name">
                {result.players.length > 1 ? `${s.playerLabel.replace("{n}", String(i + 1))} · ` : ""}
                {p.flag && <img className="summary-flag" src={`/api/flags/${p.flag}/asset`} alt="" />}
                {p.nickname}
              </p>
              <div className="summary-ring" style={{ "--pct": pct } as CSSProperties}>
                <span className="summary-pct">{pct}%</span>
              </div>
              <p className="summary-score">{s.of(p.score, p.total)}</p>
              <p className="hint">
                {s.accuracy}: {pct}% · L{result.difficulty}
              </p>
              {saved.state === "ok" && rank != null && (
                <p className="rank-chip">{s.rankChip.replace("{rank}", String(rank))}</p>
              )}
            </div>
          );
        })}
      </div>
      {saved.state === "saving" && <p className="hint">{s.saving}</p>}
      {saved.state === "fail" && <p className="error">{s.savedFail}</p>}
      <div className="summary-actions">
        <button className="next" onClick={onReplay}>
          {s.playAgain}
        </button>
        <button className="next ghost" onClick={onMenu}>
          {s.menu}
        </button>
        <button className="next ghost blue" onClick={onNewPlayer}>
          {s.newPlayer}
        </button>
      </div>
    </div>
  );
}

function Leaderboard({
  language,
  activePlayers,
}: {
  language: Lang;
  activePlayers: string[];
}) {
  const s = t(language);
  const [levels, setLevels] = useState<Record<string, ScoreEntry[]> | null>(null);
  const [worldLevels, setWorldLevels] = useState<Record<string, ScoreEntry[]> | null>(null);
  const [topFlags, setTopFlags] = useState<FlagView[] | null>(null);
  const [topScorers, setTopScorers] = useState<TopScorer[] | null>(null);

  useEffect(() => {
    let alive = true;
    const loadLevels = (scope: "fifa" | "world") =>
      fetch(`/api/leaderboard?limit=3&scope=${scope}`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("leaderboard fetch failed"))))
        .then((d) => {
          if (!alive) return;
          if (scope === "fifa") setLevels(d.levels);
          else setWorldLevels(d.levels);
        })
        .catch(() => {
          if (!alive) return;
          if (scope === "fifa") setLevels({});
          else setWorldLevels({});
        });
    loadLevels("fifa");
    loadLevels("world");
    const id = window.setInterval(() => {
      // the board is refreshed immediately on every finished game (the
      // component is remounted via `key={finishedGames}`), so the polling
      // only covers live updates: run it seldom and skip it when the tab is
      // hidden so a backgrounded game never burns requests.
      if (document.visibilityState !== "hidden") {
        loadLevels("fifa");
        loadLevels("world");
      }
    }, 30000);
    fetch("/api/leaderboard/flags?limit=10", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("leaderboard flags failed"))))
      .then((d) => alive && setTopFlags(d.flags))
      .catch(() => alive && setTopFlags([]));
    fetch("/api/topscorers?limit=10", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("top scorers failed"))))
      .then((d) => alive && setTopScorers(d.scorers))
      .catch(() => alive && setTopScorers([]));
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <aside className="leaderboard">
      <LeaderboardTable
        s={s}
        title={s.leaderboard}
        levels={levels ?? {}}
        activePlayers={activePlayers}
      />
      <LeaderboardTable
        s={s}
        title={s.leaderboardWorld}
        levels={worldLevels ?? {}}
        activePlayers={activePlayers}
      />

      <h3 className="lb-title lb-flags-title">{s.lbFlags}</h3>
      {!topFlags || topFlags.length === 0 ? (
        <p className="hint">{s.lbFlagEmpty}</p>
      ) : (
        <table className="lb-table lb-flags-table">
          <thead>
            <tr>
              <th className="lb-num">{s.lbRank}</th>
              <th>{s.lbNation}</th>
              <th className="lb-num">{s.lbViews}</th>
            </tr>
          </thead>
          <tbody>
            {topFlags.map((f, idx) => (
              <tr key={f.code}>
                <td className="lb-num">{idx + 1}</td>
                <td className="lb-player">
                  {f.asset_url && (
                    <img className="lb-flag" src={f.asset_url} alt={f.name} />
                  )}
                  <span>{f.name}</span>
                </td>
                <td className="lb-num">{f.views}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 className="lb-title lb-flags-title">{s.lbScorers}</h3>
      {!topScorers || topScorers.length === 0 ? (
        <p className="hint">{s.lbScorerEmpty}</p>
      ) : (
        <table className="lb-table lb-scorers-table">
          <thead>
            <tr>
              <th className="lb-num">{s.lbRank}</th>
              <th>{s.lbScorerPlayer}</th>
              <th>{s.lbScorerTeam}</th>
              <th className="lb-num">{s.lbScorerGoals}</th>
            </tr>
          </thead>
          <tbody>
            {topScorers.map((sc) => (
              <tr key={`${sc.rank}-${sc.player}`}>
                <td className="lb-num">{sc.rank}</td>
                <td className="lb-player">{sc.player}</td>
                <td className="lb-scorer-team">
                  {sc.asset_url && (
                    <img className="lb-flag" src={sc.asset_url} alt="" />
                  )}
                  <span>{sc.name_it ?? sc.team}</span>
                </td>
                <td className="lb-num lb-goals">{sc.goals ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  );
}

function LeaderboardTable({
  s,
  title,
  levels,
  activePlayers,
}: {
  s: Pick<(typeof T)["it"], "lbEmpty" | "lbLevel" | "lbRank" | "lbPlayer" | "lbScore">;
  title: string;
  levels: Record<string, ScoreEntry[]>;
  activePlayers: string[];
}) {
  const hasAny = Object.values(levels).some((arr) => arr.length > 0);

  return (
    <>
      <h3 className="lb-title">{title}</h3>
      {!hasAny ? (
        <p className="hint">{s.lbEmpty}</p>
      ) : (
        <table className="lb-table">
          <thead>
            <tr>
              <th>{s.lbLevel}</th>
              <th className="lb-num">{s.lbRank}</th>
              <th>{s.lbPlayer}</th>
              <th className="lb-num">{s.lbScore}</th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 7 }, (_, i) => i + 1).map((level) => {
              const top = levels[String(level)] ?? [];
              if (top.length === 0) {
                return (
                  <tr key={level} className="lb-empty-row">
                    <td>L{level}</td>
                    <td className="lb-num">—</td>
                    <td>—</td>
                    <td className="lb-num">—</td>
                  </tr>
                );
              }
              return top.slice(0, 3).map((e, idx) => (
                <tr
                  key={`${level}-${e.id}`}
                  className={activePlayers.includes(e.nickname) ? "me" : undefined}
                >
                  <td className="lb-lv">{idx === 0 ? `L${level}` : ""}</td>
                  <td className="lb-num">{idx + 1}</td>
                  <td className="lb-player">
                    {e.nickname}
                    {e.flag && <img className="lb-flag" src={`/api/flags/${e.flag}/asset`} alt="" />}
                  </td>
                  <td className="lb-num">
                    {e.score}/{e.total}
                  </td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

function Gallery({
  language,
  selected,
  onSelect,
}: {
  language: Lang;
  selected: string | null;
  onSelect: (code: string) => void;
}) {
  const s = t(language);
  const [flags, setFlags] = useState<Record<string, Option> | null>(null);
  const [query, setQuery] = useState("");
  const [continent, setContinent] = useState<string>("all");
  const [shuffled, setShuffled] = useState<[string, Option][] | null>(null);

  useEffect(() => {
    fetch(`/api/flags?language=${language}`)
      .then((r) => r.json())
      .then((d) => {
        setFlags(d.flags);
        const entries = Object.entries(d.flags as Record<string, Option>);
        for (let i = entries.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [entries[i], entries[j]] = [entries[j], entries[i]];
        }
        setShuffled(entries);
      })
      .catch(() => setFlags({}));
  }, [language]);

  if (!flags) return <p>{s.loading}</p>;

  const term = query.trim().toLowerCase();
  const entries = Object.entries(flags);
  const continents = Array.from(
    new Set(entries.map(([, f]) => f.continent).filter((c): c is string => Boolean(c)))
  ).sort();
  const matches = (shuffled ?? entries)
    .filter(([code, f]) => {
      if (continent !== "all" && (f.continent ?? "") !== continent) return false;
      if (!term) return true;
      return f.name.toLowerCase().includes(term) || code.toLowerCase().includes(term);
    })
    .sort(([, a], [, b]) =>
      continent === "all" ? 0 : a.name.localeCompare(b.name, language)
    );

  return (
    <div className="flags screen-enter">
      <h2>
        {s.gallery} ({entries.length})
      </h2>
      <input
        type="search"
        className="gallery-search"
        placeholder={s.searchFlags}
        value={query}
        autoFocus
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="continent-filter">
        <button
          type="button"
          className={"continent-chip" + (continent === "all" ? " active" : "")}
          onClick={() => setContinent("all")}
        >
          {s.continents.all}
        </button>
        {continents.map((c) => (
          <button
            key={c}
            type="button"
            className={"continent-chip" + (continent === c ? " active" : "")}
            onClick={() => setContinent(continent === c ? "all" : c)}
          >
            {(s.continents as Record<string, string>)[c] ?? c}
          </button>
        ))}
      </div>
      {matches.length === 0 ? (
        <p className="hint">{s.noResults.replace("{q}", query.trim())}</p>
      ) : (
        <div className="grid">
          {matches.map(([code, f]) => (
            <button
              key={code}
              type="button"
              className={`tile${selected === code ? " tile-selected" : ""}`}
              onClick={() => onSelect(code)}
            >
              <img src={f.asset_url ?? ""} alt={f.name} loading="lazy" decoding="async" />
              <span>{f.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function WarmupBar({
  language,
  status,
  onRefresh,
  onCleared,
}: {
  language: Lang;
  status: WarmupStatus | null;
  onRefresh: () => void;
  onCleared: () => void;
}) {
  const s = t(language);
  const [cache, setCache] = useState<{ entries: number; bytes: number } | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearedMsg, setClearedMsg] = useState(false);
  const running = status?.running ?? false;
  const total = status?.total ?? 0;
  const done = status?.done ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  const refreshCache = useCallback(() => {
    fetch("/api/flags/cache")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("cache status failed"))))
      .then(setCache)
      .catch(() => setCache(null));
  }, []);

  useEffect(() => {
    refreshCache();
  }, [refreshCache]);

  const clearCache = () => {
    if (clearing) return;
    setClearing(true);
    setClearedMsg(false);
    fetch("/api/flags/cache/clear", { method: "POST" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("clear failed"))))
      .then(() => {
        setClearedMsg(true);
        refreshCache();
        onCleared();
        window.setTimeout(() => setClearedMsg(false), 3000);
      })
      .catch(() => {})
      .finally(() => setClearing(false));
  };

  const formatBytes = (b: number) =>
    b >= 1024 * 1024
      ? `${(b / (1024 * 1024)).toFixed(1)} MB`
      : b >= 1024
        ? `${(b / 1024).toFixed(1)} KB`
        : `${b} B`;

  return (
    <div className="warmup-bar">
      <div className="warmup-info">
        {running ? (
          <span>
            {s.warmup.running.replace("{done}", String(done)).replace("{total}", String(total))}
          </span>
        ) : total > 0 ? (
          <span>{s.warmup.done.replace("{total}", String(total))}</span>
        ) : clearedMsg ? (
          <span className="warmup-cleared">{s.warmup.cleared}</span>
        ) : (
          <span>{s.warmup.hint}</span>
        )}
        {running && total > 0 && (
          <span className="warmup-progress-track">
            <span className="warmup-progress-fill" style={{ width: `${pct}%` }} />
          </span>
        )}
        <span className="warmup-cache">
          {cache && cache.entries > 0
            ? s.warmup.cacheSize.replace(
                "{size}",
                `${formatBytes(cache.bytes)} · ${cache.entries}`
              )
            : s.warmup.cacheEmpty}
        </span>
      </div>
      <button type="button" className="warmup-refresh" onClick={onRefresh} disabled={running}>
        {s.warmup.refresh}
      </button>
      <button
        type="button"
        className="warmup-clear"
        onClick={clearCache}
        disabled={running || clearing}
      >
        {clearing ? s.warmup.clearing : s.warmup.clear}
      </button>
    </div>
  );
}

const detailCache = new Map<string, FlagDetailPayload>();

function FlagDetail({
  code,
  language,
  refreshToken,
  onSelect,
}: {
  code: string;
  language: Lang;
  refreshToken: number;
  onSelect: (code: string) => void;
}) {
  const s = t(language);
  const [data, setData] = useState<FlagDetailPayload | null>(() => detailCache.get(code) ?? null);
  const [failed, setFailed] = useState(false);
  const prevRefresh = useRef(0);

  useEffect(() => {
    let alive = true;
    const isRefresh = prevRefresh.current !== refreshToken;
    prevRefresh.current = refreshToken;
    if (!isRefresh && detailCache.has(code)) {
      setData(detailCache.get(code)!);
      setFailed(false);
      return;
    }
    setFailed(false);
    setData(null);
    fetch(`/api/flags/${encodeURIComponent(code)}${isRefresh ? "?refresh=1" : ""}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("detail fetch failed"))))
      .then((d: FlagDetailPayload) => {
        detailCache.set(code, d);
        if (alive) setData(d);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [code, refreshToken]);

  const locale = language === "it" ? "it-IT" : "en-US";
  const fmt = (n: number) => n.toLocaleString(locale);
  const asset = data?.asset_url ?? `/api/flags/${encodeURIComponent(code)}/asset`;

  const rows: { label: string; value: string }[] = [];
  const d = data?.details;
  if (d) {
    if (d.full_name) rows.push({ label: s.detail.fullName, value: d.full_name });
    if (d.official_name) rows.push({ label: s.detail.officialName, value: d.official_name });
    if (d.official_languages.length > 0)
      rows.push({ label: s.detail.officialLangs, value: d.official_languages.join(", ") });
    if (d.other_languages.length > 0)
      rows.push({ label: s.detail.otherLangs, value: d.other_languages.join(", ") });
    if (d.capital) rows.push({ label: s.detail.capital, value: d.capital });
    if (d.government_form) rows.push({ label: s.detail.government, value: d.government_form });
    if (d.president) rows.push({ label: s.detail.president, value: d.president });
    if (d.proclamation) rows.push({ label: s.detail.proclamation, value: d.proclamation });
    if (d.area_km2 != null)
      rows.push({ label: s.detail.area, value: `${fmt(d.area_km2)} ${s.detail.km2}` });
    if (d.population != null)
      rows.push({ label: s.detail.population, value: `${fmt(d.population)} ${s.detail.inhabitants}` });
    if (d.density != null)
      rows.push({ label: s.detail.density, value: `${fmt(d.density)} ${s.detail.perKm2}` });
    if (d.demonym) rows.push({ label: s.detail.demonym, value: d.demonym });
    if (d.continent) rows.push({ label: s.detail.continent, value: d.continent });
    rows.push({
      label: s.detail.wcPlayed,
      value: d.football.wc > 0 ? String(d.football.wc) : "—",
    });
    rows.push({ label: s.detail.trophies, value: String(d.football.trophies) });
    if (d.football.best) rows.push({ label: s.detail.bestResults, value: d.football.best });
  }

  if (!data) {
    return (
      <aside className="flag-detail">
        <div className="detail-head">
          <img src={asset} alt={code} />
          <h3>{code}</h3>
        </div>
        <p className="hint">{failed ? s.detail.error : s.detail.loading}</p>
      </aside>
    );
  }

  return (
    <aside className="flag-detail">
      <div className="detail-head">
        <img src={data.asset_url} alt={data.name} />
        <h3>{data.name}</h3>
      </div>
      <dl className="detail-rows">
        {rows.map((r) => (
          <div key={r.label} className="detail-row">
            <dt>{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
        <div className="detail-row">
          <dt>{s.detail.borders}</dt>
          <dd>
            {d!.borders.length === 0 ? (
              <span className="hint">{s.detail.bordersNone}</span>
            ) : (
              <div className="detail-borders">
                {d!.borders.map((b) => (
                  <button
                    key={b.code}
                    type="button"
                    className="border-chip"
                    onClick={() => onSelect(b.code)}
                  >
                    <img src={b.asset_url} alt={b.name} />
                    {b.name}
                  </button>
                ))}
              </div>
            )}
          </dd>
        </div>
      </dl>
    </aside>
  );
}

export default App;
