// TaskbarGlassTAP.dll
//
// Injected into explorer.exe to make the taskbar translucent by swapping the
// taskbar's own XAML "BackgroundFill" brush in-process, exactly like an
// ExplorerTAP (TranslucentTB). Icons, tray, clock and the start button are all
// XAML elements drawn above that fill, so they stay fully visible and clickable.
//
// Loading: the launcher creates a named ready event, then installs a
// WH_CALLWNDPROC hook on the thread owning Shell_TrayWnd. When the hook fires,
// explorer maps this DLL in; DLL_PROCESS_ATTACH sees the ready event and spawns
// the install thread. The install thread calls InitializeXamlDiagnosticsEx (a
// function exported by Windows.UI.Xaml.dll), which instantiates our CLSID_TAPSite
// (IObjectWithSite) in-process and hands it an IXamlDiagnostics via SetSite.
// The TAP site then watches the visual tree for the taskbar frames and their
// BackgroundFill rectangles, so we can swap the fill brush later.
//
// Control: a named pipe server (internal) accepts "apply <alpha> <bgr>",
// "restore", "watch <pid>" and "ping". All XAML work runs on the taskbar UI
// thread through its DispatcherQueue.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <oleauto.h>
#include <xamlOM.h>
#include <sdkddkver.h>
#include <windows.ui.xaml.hosting.desktopwindowxamlsource.h>

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <unordered_set>

#ifdef GetCurrentTime
#undef GetCurrentTime
#endif

#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.System.h>
#include <winrt/Windows.UI.h>
#include <winrt/Windows.UI.Xaml.h>
#include <winrt/Windows.UI.Xaml.Shapes.h>
#include <winrt/Windows.UI.Xaml.Media.h>
#include <winrt/Windows.UI.Xaml.Hosting.h>

namespace wux = winrt::Windows::UI::Xaml;
namespace wuxm = winrt::Windows::UI::Xaml::Media;
namespace wuxs = winrt::Windows::UI::Xaml::Shapes;
namespace wuxh = winrt::Windows::UI::Xaml::Hosting;
namespace wsys = winrt::Windows::System;

// The launcher creates this named event before installing the hook; it also
// signals readiness once the visual tree is being watched.
static constexpr wchar_t kReadyEventName[] = L"TTBG_TAPReady";
// Named pipe used to talk to the injected DLL.
static constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\TTBG_TAP";
// TAP site CLSID, instantiated by the XAML diagnostics infrastructure.
static constexpr GUID kClsidTapSite = { 0x31213658, 0xfd16, 0x4328, { 0xb5, 0xe2, 0xed, 0x29, 0x03, 0xad, 0xbd, 0x2d } };

using PFN_InitializeXamlDiagnosticsEx = HRESULT(STDAPICALLTYPE*)(LPCWSTR, DWORD, LPCWSTR, LPCWSTR, CLSID, LPCWSTR);

HINSTANCE g_module = nullptr;

// ---------------------------------------------------------------------------
// Appearance service: remembers each taskbar's fill rectangles and swaps the
// BackgroundFill brush per request. Runs all XAML access on the taskbar UI
// thread via the DispatcherQueue captured at construction.
// ---------------------------------------------------------------------------
struct TaskbarAppearanceService : std::enable_shared_from_this<TaskbarAppearanceService>
{
    template <typename T>
    struct ControlInfo
    {
        T control = nullptr;
        wuxm::Brush originalFill = nullptr;
        wuxm::Brush lastBrush = nullptr;
    };

    struct TaskbarInfo
    {
        ControlInfo<wuxs::Shape> background, border;
        HWND window = nullptr;
    };

    TaskbarAppearanceService() :
        m_xamlQueue(wsys::DispatcherQueue::GetForCurrentThread())
    {
    }

    ~TaskbarAppearanceService()
    {
        if (m_waitHandle)
        {
            UnregisterWaitEx(m_waitHandle, INVALID_HANDLE_VALUE);
            m_waitHandle = nullptr;
        }
        if (m_process)
        {
            CloseHandle(m_process);
            m_process = nullptr;
        }
    }

    void Start()
    {
        try
        {
            m_pipeThread = std::thread([self = shared_from_this()]()
            {
                self->PipeLoop();
            });
        }
        catch (...)
        {
        }
    }

    void RegisterTaskbar(InstanceHandle frameHandle, HWND window)
    {
        m_taskbars[frameHandle].window = window;
    }

    void RegisterTaskbarBackground(InstanceHandle frameHandle, wuxs::Shape element)
    {
        if (const auto it = m_taskbars.find(frameHandle); it != m_taskbars.end())
        {
            it->second.background.control = element;
            it->second.background.originalFill = element.Fill();
        }
    }

    void RegisterTaskbarBorder(InstanceHandle frameHandle, wuxs::Shape element)
    {
        if (const auto it = m_taskbars.find(frameHandle); it != m_taskbars.end())
        {
            it->second.border.control = element;
            it->second.border.originalFill = element.Fill();
        }
    }

    void UnregisterTaskbar(InstanceHandle frameHandle)
    {
        m_taskbars.erase(frameHandle);
    }

    // alpha 0 = fully clear (acrylic backdrop, transparent tint), 255 = solid.
    void Apply(std::uint8_t alpha, std::uint32_t bgr)
    {
        winrt::Windows::UI::Color color{};
        color.A = alpha;
        color.R = static_cast<BYTE>(bgr & 0xFF);
        color.G = static_cast<BYTE>((bgr >> 8) & 0xFF);
        color.B = static_cast<BYTE>((bgr >> 16) & 0xFF);

        for (auto& [handle, info] : m_taskbars)
        {
            auto& bg = info.background;
            if (!bg.control)
            {
                continue;
            }

            AdoptOriginalIfNeeded(bg);

            if (alpha == 0)
            {
                // A fully transparent tint means the strip must disappear
                // completely: a zero-alpha AcrylicBrush still paints its
                // light-gray glass material, so use a plain transparent solid
                // to make the desktop behind show through exactly.
                wuxm::SolidColorBrush clear(winrt::Windows::UI::Colors::Transparent());
                bg.control.Fill(clear);
                bg.lastBrush = clear;
                continue;
            }

            wuxm::AcrylicBrush acrylic;
            // Backdrop sources what's behind the XAML, so the live desktop
            // shows through and the effect stays on while the taskbar is
            // inactive. Transparent tint = Clear; opaque tint = solid color.
            acrylic.BackgroundSource(wuxm::AcrylicBackgroundSource::Backdrop);
            acrylic.TintColor(color);
            bg.control.Fill(acrylic);
            bg.lastBrush = acrylic;
        }
    }

    void RestoreAll()
    {
        for (auto& [handle, info] : m_taskbars)
        {
            AdoptOriginalIfNeeded(info.background);
            AdoptOriginalIfNeeded(info.border);
            RestoreDefault(info.background);
            RestoreDefault(info.border);
        }
    }

    void WatchProcess(DWORD pid)
    {
        // A relaunched client re-arms the watchdog, so replace any previous
        // registration instead of ignoring the new pid.
        if (m_waitHandle)
        {
            UnregisterWaitEx(m_waitHandle, INVALID_HANDLE_VALUE);
            m_waitHandle = nullptr;
        }
        if (m_process)
        {
            CloseHandle(m_process);
            m_process = nullptr;
        }

        HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
        if (!process)
        {
            return;
        }

        auto* context = new std::weak_ptr<TaskbarAppearanceService>(weak_from_this());
        if (RegisterWaitForSingleObject(&m_waitHandle, process, &TaskbarAppearanceService::OnProcessDiedStatic, context, INFINITE, WT_EXECUTEONLYONCE))
        {
            m_process = process;
        }
        else
        {
            delete context;
            CloseHandle(process);
        }
    }

private:
    // If we have not captured a real (non-transparent, not-ours) fill yet and
    // the element currently carries one, treat it as the taskbar's own default
    // so a later RestoreAll returns it to stock instead of to a clear brush.
    static void AdoptOriginalIfNeeded(ControlInfo<wuxs::Shape>& info)
    {
        if (!info.control)
        {
            return;
        }

        try
        {
            if (info.originalFill && !IsTranslucentClear(info.originalFill))
            {
                return;
            }

            const wuxm::Brush current = info.control.Fill();
            if (current && current != info.lastBrush && !IsTranslucentClear(current))
            {
                info.originalFill = current;
            }
        }
        catch (...)
        {
        }
    }

    static bool IsTranslucentClear(const wuxm::Brush& brush) noexcept
    {
        if (!brush)
        {
            return true;
        }
        if (const auto solid = brush.try_as<wuxm::SolidColorBrush>())
        {
            return solid.Color().A < 24;
        }
        if (const auto acrylic = brush.try_as<wuxm::AcrylicBrush>())
        {
            return acrylic.TintColor().A < 24;
        }
        return false;
    }

    static void RestoreDefault(const ControlInfo<wuxs::Shape>& info)
    {
        if (!info.control || !info.originalFill)
        {
            return;
        }

        try
        {
            info.control.Fill(info.originalFill);
        }
        catch (...)
        {
        }
    }

    static void NTAPI OnProcessDiedStatic(void* parameter, BOOLEAN /*timedOut*/)
    {
        auto* weak = static_cast<std::weak_ptr<TaskbarAppearanceService>*>(parameter);
        if (const auto self = weak->lock())
        {
            wsys::DispatcherQueueHandler handler([self]()
            {
                self->RestoreAll();
            });
            self->m_xamlQueue.TryEnqueue(handler);
        }
        delete weak;
    }

    void TryXaml(std::function<void()> fn)
    {
        try
        {
            if (m_xamlQueue)
            {
                wsys::DispatcherQueueHandler handler(std::move(fn));
                m_xamlQueue.TryEnqueue(handler);
            }
        }
        catch (...)
        {
        }
    }

    void PipeLoop()
    {
        for (;;)
        {
            const HANDLE pipe = CreateNamedPipeW(kPipeName, PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                1, 4096, 4096, 0, nullptr);
            if (pipe == INVALID_HANDLE_VALUE)
            {
                return;
            }

            if (!ConnectNamedPipe(pipe, nullptr))
            {
                if (GetLastError() != ERROR_PIPE_CONNECTED)
                {
                    CloseHandle(pipe);
                    continue;
                }
            }

            char buffer[512];
            for (;;)
            {
                DWORD read = 0;
                if (!ReadFile(pipe, buffer, sizeof(buffer) - 1, &read, nullptr) || read == 0)
                {
                    break;
                }

                buffer[read] = 0;
                DispatchCommand(pipe, std::string_view(buffer, read));
            }

            FlushFileBuffers(pipe);
            DisconnectNamedPipe(pipe);
            CloseHandle(pipe);
        }
    }

    void DispatchCommand(HANDLE pipe, std::string_view line)
    {
        // Trim surrounding whitespace/newlines.
        while (!line.empty() && (line.front() == ' ' || line.front() == '\r' || line.front() == '\n'))
        {
            line.remove_prefix(1);
        }
        while (!line.empty() && (line.back() == ' ' || line.back() == '\r' || line.back() == '\n'))
        {
            line.remove_suffix(1);
        }

        if (line == "ping")
        {
            constexpr char pong[] = "pong";
            WriteFile(pipe, pong, sizeof(pong) - 1, nullptr, nullptr);
            return;
        }

        if (line == "restore")
        {
            TryXaml([self = shared_from_this()]()
            {
                self->RestoreAll();
            });
            return;
        }

        if (line.rfind("apply ", 0) == 0)
        {
            const std::string_view rest = line.substr(6);
            const auto space = rest.find(' ');
            if (space != std::string_view::npos)
            {
                try
                {
                    const int alpha = std::stoi(std::string(rest.substr(0, space)));
                    const long bgr = std::stol(std::string(rest.substr(space + 1)), nullptr, 16);
                    TryXaml([self = shared_from_this(), a = static_cast<std::uint8_t>(alpha), b = static_cast<std::uint32_t>(bgr)]()
                    {
                        self->Apply(a, b);
                    });
                }
                catch (...)
                {
                }
            }
            return;
        }

        if (line.rfind("watch ", 0) == 0)
        {
            try
            {
                const long pid = std::stol(std::string(line.substr(6)));
                WatchProcess(static_cast<DWORD>(pid));
            }
            catch (...)
            {
            }
        }
    }

    std::unordered_map<InstanceHandle, TaskbarInfo> m_taskbars;
    wsys::DispatcherQueue m_xamlQueue = nullptr;
    std::thread m_pipeThread;
    HANDLE m_process = nullptr;
    HANDLE m_waitHandle = nullptr;
};

// ---------------------------------------------------------------------------
// Visual tree watcher: finds taskbar frames + their BackgroundFill/Stroke and
// feeds them to the appearance service.
// ---------------------------------------------------------------------------
struct VisualTreeWatcher : winrt::implements<VisualTreeWatcher, IVisualTreeServiceCallback2, winrt::non_agile>
{
    VisualTreeWatcher(winrt::com_ptr<IUnknown> site, HANDLE readyEvent) :
        m_XamlDiagnostics(site.as<IXamlDiagnostics>()),
        m_AppearanceService(std::make_shared<TaskbarAppearanceService>()),
        m_ReadyEvent(readyEvent)
    {
        m_AppearanceService->Start();

        // AdviseVisualTreeChange is special-cased to bring us to the UI thread
        // for the callback. Doing it from a separate thread avoids some hangs.
        std::thread([self = get_strong()]()
        {
            try
            {
                winrt::check_hresult(self->m_XamlDiagnostics.as<IVisualTreeService3>()->AdviseVisualTreeChange(self.get()));
            }
            catch (...)
            {
                // Leave the ready event unsignalled state alone; explorer must not crash.
            }

            if (self->m_ReadyEvent)
            {
                SetEvent(self->m_ReadyEvent);
            }
        }).detach();
    }

private:
    HRESULT STDMETHODCALLTYPE OnVisualTreeChange(ParentChildRelation relation, VisualElement element, VisualMutationType mutationType) override
    {
        switch (mutationType)
        {
        case Add:
        {
            const std::wstring_view type{ element.Type, SysStringLen(element.Type) };
            if (type == winrt::name_of<wuxh::DesktopWindowXamlSource>())
            {
                // Can't tell yet whether this source will host a taskbar; keep
                // it around to match against TaskbarFrame additions.
                m_NonMatchingXamlSources.insert(element.Handle);
            }
            else if (type == L"Taskbar.TaskbarFrame")
            {
                // assumes DesktopWindowXamlSource -> RootGrid -> TaskbarFrame.
                const auto rootGrid = FromHandle<wux::UIElement>(relation.Parent);

                for (auto it = m_NonMatchingXamlSources.begin(); it != m_NonMatchingXamlSources.end(); ++it)
                {
                    const auto xamlSource = FromHandle<wuxh::DesktopWindowXamlSource>(*it);
                    wux::UIElement content = nullptr;
                    try
                    {
                        content = xamlSource.Content();
                    }
                    catch (const winrt::hresult_wrong_thread&)
                    {
                        continue;
                    }

                    if (content == rootGrid)
                    {
                        const auto nativeSource = xamlSource.as<IDesktopWindowXamlSourceNative>();

                        HWND hwnd = nullptr;
                        winrt::check_hresult(nativeSource->get_WindowHandle(&hwnd));

                        m_AppearanceService->RegisterTaskbar(element.Handle, hwnd);
                        m_NonMatchingXamlSources.erase(it);

                        break;
                    }
                }
            }
            else if (type == winrt::name_of<wuxs::Rectangle>())
            {
                const std::wstring_view name{ element.Name, SysStringLen(element.Name) };
                const bool isFill = (name == L"BackgroundFill");
                const bool isStroke = (name == L"BackgroundStroke");
                if (isFill || isStroke)
                {
                    if (const auto frame = FindParent(L"TaskbarFrame", FromHandle<wux::FrameworkElement>(relation.Parent)))
                    {
                        InstanceHandle frameHandle = 0;
                        winrt::check_hresult(m_XamlDiagnostics->GetHandleFromIInspectable(static_cast<::IInspectable*>(winrt::get_abi(frame)), &frameHandle));

                        const auto shape = FromHandle<wuxs::Rectangle>(element.Handle);
                        if (isFill)
                        {
                            m_AppearanceService->RegisterTaskbarBackground(frameHandle, shape);
                        }
                        else
                        {
                            m_AppearanceService->RegisterTaskbarBorder(frameHandle, shape);
                        }
                    }
                }
            }

            break;
        }

        case Remove: // only element.Handle is valid
            m_AppearanceService->UnregisterTaskbar(element.Handle);
            m_NonMatchingXamlSources.erase(element.Handle);
            break;
        }

        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnElementStateChanged(InstanceHandle, VisualElementState, LPCWSTR) noexcept override
    {
        return S_OK;
    }

    wux::FrameworkElement FindParent(std::wstring_view name, wux::FrameworkElement element)
    {
        const auto parent = wuxm::VisualTreeHelper::GetParent(element).try_as<wux::FrameworkElement>();
        if (parent)
        {
            if (parent.Name() == name)
            {
                return parent;
            }
            return FindParent(name, parent);
        }
        return nullptr;
    }

    template <typename T>
    T FromHandle(InstanceHandle handle)
    {
        winrt::Windows::Foundation::IInspectable obj;
        winrt::check_hresult(m_XamlDiagnostics->GetIInspectableFromHandle(handle, reinterpret_cast<::IInspectable**>(winrt::put_abi(obj))));
        return obj.as<T>();
    }

    winrt::com_ptr<IXamlDiagnostics> m_XamlDiagnostics;
    std::shared_ptr<TaskbarAppearanceService> m_AppearanceService;
    std::unordered_set<InstanceHandle> m_NonMatchingXamlSources;
    HANDLE m_ReadyEvent;
};

// ---------------------------------------------------------------------------
// TAP site: created by the XAML diagnostics infrastructure; receives the
// IXamlDiagnostics through IObjectWithSite::SetSite.
// ---------------------------------------------------------------------------
class TAPSite : public winrt::implements<TAPSite, IObjectWithSite, winrt::non_agile>
{
public:
    static DWORD WINAPI Install(void* /*unused*/)
    {
        HANDLE ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, kReadyEventName);
        if (!ready)
        {
            ready = CreateEventW(nullptr, TRUE, FALSE, kReadyEventName);
        }

        wchar_t dllPath[MAX_PATH]{};
        GetModuleFileNameW(g_module, dllPath, MAX_PATH);

        const winrt::handle wux(LoadLibraryExW(L"Windows.UI.Xaml.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        const auto ixde = reinterpret_cast<PFN_InitializeXamlDiagnosticsEx>(
            GetProcAddress(reinterpret_cast<HMODULE>(wux.get()), "InitializeXamlDiagnosticsEx"));

        bool initialized = false;
        if (ready && wux.get() && ixde)
        {
            const DWORD pid = GetCurrentProcessId();
            for (std::uint8_t attempts = 1; attempts <= 60; ++attempts)
            {
                // XAML diagnostics may only be initialized once per thread;
                // give each attempt a fresh thread and a fresh connection name.
                HRESULT hr = E_FAIL;
                const std::wstring connection = L"VisualDiagConnection" + std::to_wstring(attempts);
                std::thread([connection, &hr, ixde, pid, dllPath]()
                {
                    hr = ixde(connection.c_str(), pid, nullptr, dllPath, kClsidTapSite, nullptr);
                }).join();

                if (SUCCEEDED(hr))
                {
                    initialized = true;
                    break;
                }

                Sleep(500);
            }
        }

        // Pin this module inside explorer so the pipe server and the visual
        // tree watcher survive client restarts. XAML diagnostics can only be
        // initialized ONCE per process, so a relaunched client must be able to
        // talk to the service started by the first injection rather than trying
        // to initialize again.
        //
        // NOTE: this must NOT happen on the fresh thread created inside
        // DLL_PROCESS_ATTACH before the loader lock is released, or it
        // deadlocks; doing it here after the init loop is safe.
        if (initialized && dllPath[0])
        {
            LoadLibraryExW(dllPath, nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        }

        if (ready)
        {
            CloseHandle(ready);
        }

        return 0;
    }

    HRESULT STDMETHODCALLTYPE SetSite(IUnknown* pUnkSite) override try
    {
        if (s_Watcher.get())
        {
            return E_ILLEGAL_METHOD_CALL;
        }

        site.copy_from(pUnkSite);

        if (site)
        {
            HANDLE ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, kReadyEventName);
            s_Watcher = winrt::make_self<VisualTreeWatcher>(site, ready);
        }

        return S_OK;
    }
    catch (...)
    {
        return winrt::to_hresult();
    }

    HRESULT STDMETHODCALLTYPE GetSite(REFIID riid, void** ppvSite) noexcept override
    {
        return site.as(riid, ppvSite);
    }

private:
    static winrt::weak_ref<VisualTreeWatcher> s_Watcher;
    winrt::com_ptr<IUnknown> site;
};

winrt::weak_ref<VisualTreeWatcher> TAPSite::s_Watcher{ nullptr };

// ---------------------------------------------------------------------------
// Class factory for the TAP site.
// ---------------------------------------------------------------------------
template <class T>
struct SimpleFactory : winrt::implements<SimpleFactory<T>, IClassFactory, winrt::non_agile>
{
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppvObject) override
    {
        if (pUnkOuter)
        {
            return CLASS_E_NOAGGREGATION;
        }

        try
        {
            *ppvObject = nullptr;
            return winrt::make<T>().as(riid, ppvObject);
        }
        catch (...)
        {
            return winrt::to_hresult();
        }
    }

    HRESULT STDMETHODCALLTYPE LockServer(BOOL fLock) noexcept override
    {
        if (fLock)
        {
            ++winrt::get_module_lock();
        }
        else
        {
            --winrt::get_module_lock();
        }

        return S_OK;
    }
};

// ---------------------------------------------------------------------------
// Exports.
// ---------------------------------------------------------------------------
extern "C" __declspec(dllexport) LRESULT CALLBACK TapHookWndProc(int nCode, WPARAM wParam, LPARAM lParam)
{
    return CallNextHookEx(nullptr, nCode, wParam, lParam);
}

_Use_decl_annotations_
STDAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID* ppv)
{
    try
    {
        if (rclsid == kClsidTapSite)
        {
            *ppv = nullptr;
            return winrt::make<SimpleFactory<TAPSite>>().as(riid, ppv);
        }

        return CLASS_E_CLASSNOTAVAILABLE;
    }
    catch (...)
    {
        return winrt::to_hresult();
    }
}

// ---------------------------------------------------------------------------
// Entry point.
// ---------------------------------------------------------------------------
BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID /*lpvReserved*/) noexcept
{
    switch (fdwReason)
    {
    case DLL_PROCESS_ATTACH:
    {
        g_module = hinstDLL;
        DisableThreadLibraryCalls(hinstDLL);

        // Only install when this load was requested by the launcher: the ready
        // event is created before the hook is installed.
        const HANDLE marker = OpenEventW(EVENT_MODIFY_STATE, FALSE, kReadyEventName);
        if (marker)
        {
            CloseHandle(marker);
            const HANDLE thread = CreateThread(nullptr, 0, &TAPSite::Install, nullptr, 0, nullptr);
            if (thread)
            {
                CloseHandle(thread);
            }
        }
        break;
    }

    case DLL_PROCESS_DETACH:
        break;
    }

    return TRUE;
}