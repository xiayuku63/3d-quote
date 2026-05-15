/**
 * bambu_cli — Pure CLI headless BambuStudio Slicer
 *
 * Links ONLY against libslic3r (no wxWidgets, no GUI).
 * Loads Bambu JSON presets, slices STL/3MF/STEP, exports G-code.
 *
 * Build:
 *   Place this file in the BambuStudio source root.
 *   cmake -B build -S . -DSLIC3R_BUILD_CLI=ON
 *   cmake --build build --target bambu_cli
 *
 * Usage:
 *   bambu_cli \
 *     --printer profiles/bambu/machine.json \
 *     --process profiles/bambu/process.json \
 *     --filament profiles/bambu/filament.json \
 *     --output out.gcode \
 *     model.stl
 */

#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <cstdlib>
#include <chrono>

// libslic3r headers (no wx dependency)
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/GCode.hpp"
#include "libslic3r/Config.hpp"
#include "libslic3r/Utils.hpp"
#include "libslic3r/Point.hpp"
#include "libslic3r/Geometry.hpp"
#include "libslic3r/Format/STL.hpp"
#include "libslic3r/Format/AMF.hpp"
#include "libslic3r/Format/3MF.hpp"
#include "libslic3r/Platform.hpp"

// In BambuStudio, Preset loading for JSON:
// PresetBundle can load from directories or individual files
// For pure CLI, we load JSON directly into DynamicPrintConfig
#include "libslic3r/Preset.hpp"
#include "libslic3r/PresetBundle.hpp"

// Boost filesystem & property tree for JSON loading
#include <boost/filesystem.hpp>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>

namespace fs = boost::filesystem;
namespace pt = boost::property_tree;

// ---------------------------------------------------------------------------
// Config loading from Bambu JSON presets
// ---------------------------------------------------------------------------

/**
 * Bambu Studio stores presets as JSON with structure:
 *   { "name": "My Printer", "inherits": "base_printer",
 *     "type": "machine",
 *     "is_custom_defined": "1",
 *     "printable_area": [...],
 *     "machine_extruder_count": 1,
 *     ... }
 *
 * We flatten all key=value pairs (excluding metadata keys)
 * into a DynamicPrintConfig.
 */

// Metadata keys that are NOT print config keys
static const std::vector<std::string> PRESET_META_KEYS = {
    "name", "inherits", "type", "from", "instantiation",
    "is_custom_defined", "upgrade_version", "version",
    "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition",
    "setting_id", "template_custom_gcode", "thumbnails",
    "filament_vendor", "filament_settings_id", "filament_id",
    "printable_area", "printable_height",
    "description", "author", "license",
};

static bool is_meta_key(const std::string& key) {
    if (key.empty()) return true;
    if (key[0] == '_' || key[0] == '#') return true;
    for (const auto& mk : PRESET_META_KEYS) {
        if (key == mk) return true;
    }
    return false;
}

/**
 * Load a Bambu JSON preset file and merge config keys into `config`.
 */
static bool load_json_preset(const std::string& path,
                              Slic3r::DynamicPrintConfig& config) {
    if (!fs::exists(path)) {
        std::cerr << "[WARN] Preset not found: " << path << std::endl;
        return false;
    }

    pt::ptree tree;
    try {
        pt::read_json(path, tree);
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] Failed to parse JSON: " << path
                  << " — " << e.what() << std::endl;
        return false;
    }

    int loaded = 0;
    for (const auto& kv : tree) {
        const std::string& key = kv.first;
        if (is_meta_key(key)) continue;

        const auto& val_node = kv.second;
        std::string val_str;

        // Extract value as string
        if (val_node.empty()) {
            continue;  // skip empty values
        } else if (auto v = val_node.get_value_optional<std::string>()) {
            val_str = *v;
        } else if (auto v = val_node.get_value_optional<int>()) {
            val_str = std::to_string(*v);
        } else if (auto v = val_node.get_value_optional<double>()) {
            val_str = std::to_string(*v);
        } else if (auto v = val_node.get_value_optional<float>()) {
            val_str = std::to_string(*v);
        } else if (auto v = val_node.get_value_optional<bool>()) {
            val_str = *v ? "1" : "0";
        } else {
            // Arrays or nested objects — try to get as string
            std::ostringstream oss;
            pt::write_json(oss, val_node, false);
            val_str = oss.str();
        }

        if (val_str.empty()) continue;

        // Set the config option. DynamicPrintConfig::set_deserialize
        // handles type coercion from string.
        try {
            config.set_deserialize(key, val_str, Slic3r::ConfigSubstitutionContext::default_context());
            loaded++;
        } catch (const std::exception&) {
            // Unknown key or parse error — silently skip
        }
    }

    std::cerr << "[INFO] Loaded " << loaded << " config keys from "
              << fs::path(path).filename() << std::endl;
    return loaded > 0;
}

/**
 * Apply CLI overrides from --set key=value arguments.
 */
static void apply_cli_overrides(Slic3r::DynamicPrintConfig& config,
                                 const std::vector<std::string>& overrides) {
    for (const auto& ov : overrides) {
        auto eq = ov.find('=');
        if (eq == std::string::npos) continue;
        std::string key = ov.substr(0, eq);
        std::string val = ov.substr(eq + 1);
        try {
            config.set_deserialize(key, val, Slic3r::ConfigSubstitutionContext::default_context());
            std::cerr << "[INFO] Override: " << key << " = " << val << std::endl;
        } catch (const std::exception& e) {
            std::cerr << "[WARN] Skipping unknown override: " << key
                      << " (" << e.what() << ")" << std::endl;
        }
    }
}

// ---------------------------------------------------------------------------
// G-code stats parsing (same logic as Python parser)
// ---------------------------------------------------------------------------

struct GCodeStats {
    double filament_mm = 0.0;
    double filament_cm3 = 0.0;
    double filament_g = 0.0;
    int time_s = 0;
    std::string time_str;
};

static GCodeStats parse_gcode_stats(const std::string& gcode) {
    GCodeStats stats;
    std::istringstream iss(gcode);
    std::string line;
    while (std::getline(iss, line)) {
        // BambuStudio G-code comments:
        // ; filament used [mm] = 428.05
        // ; filament used [cm3] = 1.03
        // ; total filament used [g] = 1.28
        // ; estimated printing time (normal mode) = 1h 44m 3s
        // ; estimated printing time = 1:44:03

        if (line.rfind("; filament used [mm]", 0) == 0) {
            auto pos = line.find('=');
            if (pos != std::string::npos)
                stats.filament_mm = std::atof(line.substr(pos + 1).c_str());
        }
        else if (line.rfind("; filament used [cm3]", 0) == 0) {
            auto pos = line.find('=');
            if (pos != std::string::npos)
                stats.filament_cm3 = std::atof(line.substr(pos + 1).c_str());
        }
        else if (line.rfind("; total filament used [g]", 0) == 0 ||
                 line.rfind("; total filament used [g] ", 0) == 0) {
            auto pos = line.rfind('=');
            if (pos != std::string::npos)
                stats.filament_g = std::atof(line.substr(pos + 1).c_str());
        }
        else if (line.find("estimated printing time (normal mode)") != std::string::npos) {
            auto pos = line.find('=');
            if (pos != std::string::npos) {
                std::string time_part = line.substr(pos + 1);
                // Parse "1h 44m 3s" or "44m 3s"
                int h = 0, m = 0, s = 0;
                Slic3r::Utils::parse_print_time(time_part, h, m, s);
                stats.time_s = h * 3600 + m * 60 + s;
                stats.time_str = time_part;
            }
        }
    }
    return stats;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

static void print_usage(const char* prog) {
    std::cerr << "Usage: " << prog << " [OPTIONS] model.stl\n"
              << "\n"
              << "BambuStudio pure-CLI slicer (no GUI, no wxWidgets)\n"
              << "\n"
              << "Presets:\n"
              << "  --printer FILE     Bambu machine JSON profile\n"
              << "  --process FILE     Bambu process JSON profile\n"
              << "  --filament FILE    Bambu filament JSON profile\n"
              << "\n"
              << "Output:\n"
              << "  --output FILE      Output G-code path (required)\n"
              << "  --export-3mf FILE  Also export 3MF project file\n"
              << "  --stats            Print filament/time stats to stdout as JSON\n"
              << "\n"
              << "Overrides:\n"
              << "  --set KEY=VALUE    Override any print config key\n"
              << "  --layer-height MM  Shorthand for --set layer_height=MM\n"
              << "  --infill PERCENT   Shorthand for --set fill_density=PERCENT%\n"
              << "\n"
              << "Diagnostics:\n"
              << "  --version          Print version and exit\n"
              << "  --help             This message\n"
              << std::endl;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    // ── Parse CLI arguments ──
    std::string printer_json, process_json, filament_json;
    std::string output_gcode, output_3mf;
    std::string model_path;
    std::vector<std::string> overrides;
    bool stats_flag = false;

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        } else if (arg == "--version") {
            std::cout << "bambu_cli v0.1.0 (BambuStudio libslic3r)" << std::endl;
            return 0;
        } else if (arg == "--printer" && i + 1 < argc) {
            printer_json = argv[++i];
        } else if (arg == "--process" && i + 1 < argc) {
            process_json = argv[++i];
        } else if (arg == "--filament" && i + 1 < argc) {
            filament_json = argv[++i];
        } else if (arg == "--output" && i + 1 < argc) {
            output_gcode = argv[++i];
        } else if (arg == "--export-3mf" && i + 1 < argc) {
            output_3mf = argv[++i];
        } else if (arg == "--layer-height" && i + 1 < argc) {
            overrides.push_back(std::string("layer_height=") + argv[++i]);
        } else if (arg == "--infill" && i + 1 < argc) {
            overrides.push_back(std::string("fill_density=") + argv[++i] + "%");
        } else if (arg == "--set" && i + 1 < argc) {
            overrides.push_back(argv[++i]);
        } else if (arg == "--stats") {
            stats_flag = true;
        } else if (arg[0] != '-') {
            model_path = arg;
        } else {
            std::cerr << "[ERROR] Unknown option: " << arg << std::endl;
            return 1;
        }
    }

    if (model_path.empty()) {
        std::cerr << "[ERROR] No model file specified." << std::endl;
        return 1;
    }
    if (output_gcode.empty()) {
        output_gcode = fs::path(model_path).stem().string() + ".gcode";
        std::cerr << "[INFO] Output defaulting to: " << output_gcode << std::endl;
    }
    if (!fs::exists(model_path)) {
        std::cerr << "[ERROR] Model file not found: " << model_path << std::endl;
        return 1;
    }

    // ── Load presets ──
    Slic3r::DynamicPrintConfig print_config;
    print_config.normalize_fdm();

    // Defaults (Bambu A1 0.4mm basic)
    print_config.set_deserialize("layer_height", "0.2", Slic3r::ConfigSubstitutionContext::default_context());
    print_config.set_deserialize("nozzle_diameter", "0.4", Slic3r::ConfigSubstitutionContext::default_context());

    if (!printer_json.empty()) load_json_preset(printer_json, print_config);
    if (!process_json.empty()) load_json_preset(process_json, print_config);
    if (!filament_json.empty()) load_json_preset(filament_json, print_config);
    apply_cli_overrides(print_config, overrides);

    // ── Load model ──
    std::cerr << "[INFO] Loading model: " << model_path << std::endl;
    auto t_load_start = std::chrono::steady_clock::now();

    Slic3r::Model model;
    try {
        // Determine format from extension
        std::string ext = fs::path(model_path).extension().string();
        std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);

        if (ext == ".stl") {
            Slic3r::load_stl(model_path.c_str(), &model);
        } else if (ext == ".3mf") {
            Slic3r::DynamicPrintConfig unused_config;
            Slic3r::load_3mf(model_path.c_str(), unused_config, &model,
                             nullptr, Slic3r::Load3mfStrategy::LoadModel);
        } else if (ext == ".step" || ext == ".stp") {
            Slic3r::load_step(model_path.c_str(), &model);
        } else if (ext == ".obj") {
            Slic3r::load_obj(model_path.c_str(), &model);
        } else if (ext == ".amf") {
            Slic3r::load_amf(model_path.c_str(), &model, nullptr);
        } else {
            // Try STL as default
            Slic3r::load_stl(model_path.c_str(), &model);
        }
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] Failed to load model: " << e.what() << std::endl;
        return 1;
    }

    if (model.objects.empty()) {
        std::cerr << "[ERROR] Model has no objects." << std::endl;
        return 1;
    }

    // Center and arrange
    model.center_instances_around_point(Slic3r::Vec2d(128.0, 128.0));  // A1 bed center
    model.arrange_objects(Slic3r::Vec2d(256.0, 256.0),  // A1 bed size
                          Slic3r::ArrangeParams());

    auto t_load = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - t_load_start);
    std::cerr << "[INFO] Model loaded (" << model.objects.size()
              << " objects) in " << t_load.count() << "ms" << std::endl;

    // ── Validate config ──
    auto validation = print_config.validate();
    if (!validation.empty()) {
        std::cerr << "[WARN] Config validation warnings:" << std::endl;
        for (const auto& w : validation) {
            std::cerr << "  - " << w << std::endl;
        }
    }

    // ── Slice ──
    std::cerr << "[INFO] Slicing..." << std::endl;
    auto t_slice_start = std::chrono::steady_clock::now();

    Slic3r::Print print;
    Slic3r::PrintStatistics print_stats;

    try {
        // Set up status callback for progress
        print.set_status_callback([](int percent, const std::string& msg) {
            if (!msg.empty()) {
                std::cerr << "  [" << percent << "%] " << msg << std::endl;
            }
        });

        // Apply configuration
        print.apply(model, print_config);

        // Process (the actual slicing)
        print.process();

        print_stats = print.print_statistics();

    } catch (const std::exception& e) {
        std::cerr << "[ERROR] Slicing failed: " << e.what() << std::endl;
        return 1;
    }

    auto t_slice = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - t_slice_start);
    std::cerr << "[INFO] Sliced in " << t_slice.count() << "ms" << std::endl;

    // ── Export G-code ──
    std::cerr << "[INFO] Exporting G-code: " << output_gcode << std::endl;

    {
        std::ofstream ofs(output_gcode, std::ios::binary);
        if (!ofs) {
            std::cerr << "[ERROR] Cannot write: " << output_gcode << std::endl;
            return 1;
        }

        Slic3r::GCode gcode;
        Slic3r::GCodeGeneratorResult result;

        try {
            gcode.do_export(print, output_gcode,
                           Slic3r::GCodeProcessorResult(),
                           Slic3r::ThumbnailsList(),
                           nullptr,  // plate source
                           nullptr); // post-process script
        } catch (const std::exception& e) {
            std::cerr << "[ERROR] G-code export failed: " << e.what() << std::endl;
            return 1;
        }
    }

    std::cerr << "[INFO] G-code written: " << output_gcode
              << " (" << fs::file_size(output_gcode) / 1024 << " KB)" << std::endl;

    // ── Export 3MF (optional) ──
    if (!output_3mf.empty()) {
        std::cerr << "[INFO] Exporting 3MF: " << output_3mf << std::endl;
        try {
            Slic3r::store_3mf(output_3mf.c_str(), &model, &print_config,
                             nullptr, false);
            std::cerr << "[INFO] 3MF written: " << output_3mf << std::endl;
        } catch (const std::exception& e) {
            std::cerr << "[WARN] 3MF export failed: " << e.what() << std::endl;
        }
    }

    // ── Print statistics ──
    if (stats_flag) {
        std::cout << "{" << std::endl;
        std::cout << "  \"filament_mm\": " << print_stats.total_used_filament << "," << std::endl;
        std::cout << "  \"filament_cm3\": " << print_stats.total_extruded_volume << "," << std::endl;
        std::cout << "  \"estimated_time_s\": " << print_stats.estimated_normal_print_time << "," << std::endl;
        std::cout << "  \"total_cost\": " << print_stats.total_cost << "," << std::endl;
        std::cout << "  \"total_weight\": " << print_stats.total_weight << std::endl;
        std::cout << "}" << std::endl;
    }

    std::cerr << "[INFO] Done." << std::endl;

    // Print human-readable summary
    if (print_stats.estimated_normal_print_time > 0) {
        int s = (int)print_stats.estimated_normal_print_time;
        int h = s / 3600, m = (s % 3600) / 60;
        s = s % 60;
        std::cerr << "\n═══════════════════════════════════" << std::endl;
        std::cerr << "  Estimated time: ";
        if (h > 0) std::cerr << h << "h ";
        std::cerr << m << "m " << s << "s" << std::endl;
        std::cerr << "  Filament used:  "
                  << std::fixed << std::setprecision(2)
                  << print_stats.total_used_filament << " mm" << std::endl;
        std::cerr << "  Filament volume: "
                  << std::fixed << std::setprecision(2)
                  << print_stats.total_extruded_volume << " cm³" << std::endl;
        std::cerr << "═══════════════════════════════════" << std::endl;
    }

    return 0;
}
